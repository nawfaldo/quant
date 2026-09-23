//! One live strategy on one MT5 account: the slot that steps it, turns its
//! actions into execution commands, and reconciles what the broker actually did
//! against what the strategy believes it holds.

use super::*;

pub(super) fn normalize_history(history: &mut Vec<Bar>, latest: i64) {
    history.retain(|bar| bar.ts <= latest);
    history.sort_by_key(|bar| bar.ts);
    history.dedup_by_key(|bar| bar.ts);
}

#[derive(Clone)]
pub(super) struct LogicalPosition {
    pub(super) side: Side,
    pub(super) volume: f64,
}

/// Whether a failure says nothing about the correctness of the live book.
/// Database and internal errors are infrastructure: the right response is to
/// try again. Everything else is an invariant this runtime asserted itself —
/// a duplicate position key, a volume outside the broker's limits — and means
/// the book can no longer be trusted, which is what `blocked` is for.
pub(super) fn is_transient(error: &crate::error::ApiError) -> bool {
    matches!(
        error,
        crate::error::ApiError::SeaOrm(_) | crate::error::ApiError::Internal(_)
    )
}

/// Whether an action reduces exposure. Exits are exempt from every refusal the
/// runtime applies to entries: refusing to open is safe, refusing to close is
/// not.
pub(super) fn is_exit(action: Action) -> bool {
    matches!(action, Action::Close { .. } | Action::ClosePosition { .. })
}

/// Why a slot stopped taking entries, and therefore whether it can start again
/// on its own.
///
/// THE TWO ARE NOT THE SAME KIND OF FACT AND SHARING ONE `bool` COST A SESSION.
/// `Ownership` is an ASSERTION ABOUT RIGHT NOW -- the broker's open positions do
/// not match the durable rows -- and it is recomputed from scratch every
/// refresh. `Sticky` is a HISTORICAL EVENT: a feed died, an action failed, the
/// replayed book disagreed at warm-up. Nothing later can prove those did not
/// happen, so only a person retires them.
///
/// As one `bool` every block was sticky, including the ownership one, which
/// goes spurious on its own: the check compares `mt5_strategy_positions`,
/// written when an execution command completes, against `mt5_bridge_positions`,
/// pushed by the bridge, so a snapshot landing between a fill and the durable
/// write is a mismatch that resolves itself a moment later. On 2026-09-09
/// `jp225_volume_thrust` and `jp225_volatility_breakout` were blocked at
/// 02:42Z, three hours before the operator's session, and never took another
/// entry all day. Restarting did not help -- `jp225_volume_thrust` was blocked
/// AGAIN at 04:16Z, the rebuilt slot re-running the same check -- which is the
/// evidence that the flag was not the problem; never re-evaluating it was.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum BlockReason {
    /// Broker and durable ownership disagree. Cleared the moment they agree.
    Ownership,
    /// The runtime lost sight of this market and flattened on the watchdog.
    /// Cleared when that market's feed advances again, because "we cannot see
    /// it" is the most re-checkable claim in here: a bar arriving IS the proof.
    ///
    /// It used to be sticky, which meant one vendor hiccup retired a sleeve for
    /// the rest of the day -- and vendor hiccups are routine, three of them on
    /// 2026-09-09 alone. The flatten still happens; what changes is that the
    /// sleeve is handed back once the runtime can see the market again.
    Feed,
    /// Anything a later observation cannot disprove: an invariant this runtime
    /// asserted and found broken, or a replay that disagrees with the durable
    /// book. Cleared only by rebuilding the slot -- a restart, or a change to
    /// the active strategy set.
    Sticky,
}

/// An exit the strategy has already asked for that has not reached a broker
/// command yet.
#[derive(Clone, Copy)]
pub(super) struct PendingExit {
    pub(super) price: f64,
    pub(super) fraction: f64,
}

#[derive(Clone, Copy)]
pub(super) struct PendingOpen {
    pub(super) command_id: i64,
    pub(super) action: Action,
}

#[derive(Clone, Copy)]
pub(super) struct DeferredEntry {
    pub(super) action: Action,
    pub(super) signal_bar: Bar,
}

pub(super) struct LiveSlot {
    pub(super) target: LiveStrategyTarget,
    pub(super) strategy: Box<dyn Strategy>,
    pub(super) positions: HashMap<String, LogicalPosition>,
    /// Entries emitted by the strategy but not yet acknowledged by MT5.
    /// Keeping the original action lets `discard` undo strategy state if the
    /// asynchronous broker command is rejected.
    pub(super) pending_opens: HashMap<String, PendingOpen>,
    /// Replacement entries waiting for the previous broker leg to confirm its
    /// close. Drift can close and reopen in one update; sending the new order
    /// before the close result races ownership of the same position key.
    pub(super) deferred_entries: HashMap<String, DeferredEntry>,
    /// Exits still owed to the broker, keyed by position. The backtest closes
    /// instantly and cannot fail, so a strategy emits each exit exactly once and
    /// then forgets the position. Live has to enqueue a command that can fail,
    /// and when it did the exit was dropped and the position rode to the
    /// end-of-day flatten — a 12:24 exit recorded at 16:59. Owning the retry
    /// here is what makes a live close land where the backtest's does.
    pub(super) pending_exits: HashMap<String, PendingExit>,
    /// `None` while the sleeve is taking entries. See `BlockReason`.
    pub(super) blocked: Option<BlockReason>,
}

impl LiveSlot {
    /// Durable MT5 ownership is authoritative for the volume of an already-open
    /// position: a restart has today's balance but cannot reconstruct every
    /// historical fill and balance transition, so comparing replayed lots would
    /// create false mismatches.
    pub(super) async fn warm(
        target: LiveStrategyTarget,
        history: &[Bar],
        db: &Database,
    ) -> Result<Self, crate::error::ApiError> {
        // Refused rather than corrected. A row whose symbol does not name the
        // sleeve's own instrument is a configuration mistake, and quietly
        // rewriting it would hide a person's intent to trade something else;
        // trading it as written would send real orders to the wrong contract.
        if !symbol_matches_strategy(&target.strategy, &target.symbol) {
            return Err(crate::error::ApiError::BadRequest(format!(
                "{} is configured with symbol {:?} but trades {}",
                target.strategy,
                target.symbol,
                required_symbol(&target.strategy).unwrap_or("nothing"),
            )));
        }
        let mut slot = Self {
            strategy: build_strategy(&target.strategy).ok_or_else(|| {
                crate::error::ApiError::BadRequest("strategy is not live-capable".into())
            })?,
            target,
            positions: HashMap::new(),
            pending_opens: HashMap::new(),
            deferred_entries: HashMap::new(),
            pending_exits: HashMap::new(),
            blocked: None,
        };
        slot.replay(history);
        let persisted = db
            .strategy_positions(slot.target.account_strategy_id)
            .await?;
        slot.reconcile(db, &persisted).await;
        Ok(slot)
    }

    /// Rebuilds strategy state from `history`, with no database and no orders.
    ///
    /// Split out of `warm` so the preroll boundary below can be tested: every
    /// other step of a warm-up needs SQLite, and this one needs nothing.
    pub(super) fn replay(&mut self, history: &[Bar]) {
        // Whether the replay has crossed out of the preroll and into the bars
        // the account actually traded. See `reset_trading_state` below.
        let mut trading = false;
        for bar in history {
            // THE PRERROLL/LIVE BOUNDARY IS THE ENGINE'S `lo`, AND THE STRATEGY
            // HAS TO BE TOLD WHERE IT IS.
            //
            // `FamilyEngine` carries a SHADOW ACCOUNT that decides which trades
            // exist at all: it starts at `ADMISSION_BALANCE` and compounds each
            // sleeve's standalone P&L, and `reset_trading_state` is what puts it
            // back at the start of a reported window. `engine.rs` calls it at
            // the preroll boundary for exactly this reason -- on
            // `jp225:swing_donchian` a shadow that had compounded through the
            // preroll admitted five trades Python refused.
            //
            // Live has the same boundary and never announced it, so every slot
            // answered the admission question against a balance that had been
            // compounding through a hundred sessions of warm-up. Announce it
            // here, once, on the first bar the account is live for.
            if !trading && bar.ts > self.target.live_started_at {
                trading = true;
                self.strategy.reset_trading_state();
            }
            let actions = self.strategy.update_all(*bar, self.target.equity.max(1.0));
            if bar.ts <= self.target.live_started_at {
                self.strategy.discard_all(actions);
            } else {
                for action in actions {
                    self.apply_expected(action);
                }
                // Replay has to flatten where the live loop would have, or a
                // position the strategy silently abandoned at its session end
                // survives in the replayed book, `reconcile` sees a phantom
                // against the durable MT5 rows and blocks the strategy on every
                // restart. Bookkeeping only: the real close was already sent.
                if self.at_session_end(*bar) {
                    self.positions.clear();
                }
            }
        }
        // A strategy activated just now has a `live_started_at` at or after the
        // newest warm-up bar, so the boundary above is never crossed inside the
        // loop -- and that is the case that matters most, because it is every
        // first start. The whole replay was preroll; announce the boundary at
        // its end instead. Nothing is lost: every action was discarded, so
        // `positions` is empty either way.
        if !trading {
            self.strategy.reset_trading_state();
        }
    }

    pub(super) async fn reconcile(&mut self, db: &Database, persisted: &[StrategyPosition]) {
        let persisted_open: HashMap<&str, &StrategyPosition> = persisted
            .iter()
            .filter(|position| position.status != "pending_close")
            .map(|position| (position.position_key.as_str(), position))
            .collect();
        // THE THREE DISAGREEMENTS ARE NOT ONE FACT, AND TREATING THEM AS ONE
        // COST A SLEEVE A SESSION EVERY TIME THE STACK STARTED LATE.
        //
        //   phantom   the replay holds something the broker never took. The
        //             replay takes every entry unconditionally; the runtime may
        //             simply not have been running when that one fired. Nothing
        //             unsafe exists -- there is no position anywhere.
        //   orphan    the BROKER holds something the replay does not know about.
        //             A real position with nobody managing it.
        //   inverted  both hold the key, on opposite sides. Whatever else is
        //             true, one of the two books is wrong about direction.
        //
        // Only the first is recoverable, and it is the only one that has ever
        // fired in anger: 7 of the 9 blocks ever recorded, hitting 5 of 22
        // sleeves in four live days, every one of them at a server start.
        // `audusd_zscore` was blocked on 2026-09-11 for a position it had never
        // sent an order for, never held, and that the broker had never seen.
        //
        // The other two still block. Refusing entries does not manage an orphan
        // position, but it does stop the runtime adding risk on top of a book it
        // has been shown to be wrong about.
        let phantoms: Vec<String> = self
            .positions
            .keys()
            .filter(|key| !persisted_open.contains_key(key.as_str()))
            .cloned()
            .collect();
        let orphans = persisted_open
            .keys()
            .filter(|key| !self.positions.contains_key(**key))
            .count();
        let inverted = self.positions.iter().any(|(key, expected)| {
            persisted_open
                .get(key.as_str())
                .is_some_and(|actual| actual.side != side_name(expected.side))
        });
        if orphans > 0 || inverted {
            self.blocked = Some(BlockReason::Sticky);
            tracing::error!(
                account_strategy_id = self.target.account_strategy_id,
                strategy = self.target.strategy,
                orphans,
                inverted,
                "live strategy state does not match durable MT5 positions; entries blocked"
            );
            db.log_live_event(
                self.target.account_strategy_id,
                &self.target.strategy,
                "blocked",
                "",
                "",
                &format!(
                    "replayed book has {} positions, durable MT5 has {}",
                    self.positions.len(),
                    persisted_open.len()
                ),
            )
            .await;
        } else if !phantoms.is_empty() {
            // SKIP THE TRADE, TAKE THE NEXT SIGNAL. The strategy has to be told
            // as well as the slot: clearing only `positions` leaves the strategy
            // believing it is in, and it then emits a close for a position the
            // slot no longer has -- the "strategy attempted to close unknown
            // position" path.
            self.strategy.abandon_open_position();
            for key in &phantoms {
                self.positions.remove(key);
                self.pending_exits.remove(key);
                self.deferred_entries.remove(key);
            }
            tracing::warn!(
                account_strategy_id = self.target.account_strategy_id,
                strategy = self.target.strategy,
                dropped = phantoms.len(),
                "replayed entry was never sent live; skipping it and taking the next signal"
            );
            db.log_live_event(
                self.target.account_strategy_id,
                &self.target.strategy,
                "replay_phantom_skipped",
                &phantoms.join(","),
                "",
                &format!(
                    "replay held {} position(s) the broker never took; skipped,                      entries stay open",
                    phantoms.len()
                ),
            )
            .await;
        } else {
            // Signal replay proves which position should exist and on which
            // side; the broker-owned durable row supplies the quantity that was
            // actually filled.
            for (key, expected) in &mut self.positions {
                if let Some(actual) = persisted_open.get(key.as_str()) {
                    expected.volume = actual.remaining_volume;
                }
            }
        }
    }

    pub(super) async fn on_bar(&mut self, db: &Database, bar: Bar, entries_current: bool) {
        self.settle_pending_opens(db, bar).await;
        self.retry_pending_exits(db, bar).await;
        let actions = self.strategy.update_all(bar, self.target.equity.max(0.0));
        for action in actions {
            let is_exit = is_exit(action);
            // `blocked` means "do not take on new risk", so it must not swallow
            // exits. Discarding an exit cannot make the book safer: the strategy
            // forgets the position either way and the broker leg is left open
            // until the end-of-day flatten.
            if self.blocked.is_some() && !is_exit {
                self.strategy.discard(action);
                // RECORDED, BECAUSE THIS USED TO BE THE QUIETEST LOSS IN THE
                // RUNTIME. A blocked sleeve dropped every entry it wanted with
                // no event and no log line, so the only evidence a trade had
                // been skipped was the block itself -- hours earlier, and on a
                // different screen. On 2026-09-09 `jp225_volume_thrust` and
                // `jp225_volatility_breakout` were blocked at 02:42Z, before
                // the session even started, and `live_events` recorded exactly
                // one refusal all day: a different sleeve's.
                //
                // Not spam: an entry ACTION is emitted only when the strategy
                // actually wants the trade, not once a bar.
                tracing::warn!(
                    account_strategy_id = self.target.account_strategy_id,
                    strategy = self.target.strategy,
                    "entry dropped: strategy is blocked"
                );
                self.log_event(db, "entry_refused", "", bar, "strategy is blocked")
                    .await;
                continue;
            }
            if self.is_settled_close(action, bar) {
                self.strategy.discard(action);
                continue;
            }
            if let Some(reason) = self.entry_refusal(action, entries_current) {
                self.strategy.discard(action);
                tracing::warn!(
                    reason,
                    account_strategy_id = self.target.account_strategy_id,
                    strategy = self.target.strategy,
                    "live entry refused"
                );
                self.log_event(db, "entry_refused", "", bar, reason).await;
                continue;
            }
            if let Err(error) = self.execute(db, action, bar).await {
                self.strategy.discard(action);
                if is_exit {
                    // Owed, not abandoned. Only entries can block.
                    self.owe_exit(action);
                    tracing::error!(
                        %error,
                        account_strategy_id = self.target.account_strategy_id,
                        strategy = self.target.strategy,
                        "live exit failed; queued for retry"
                    );
                    self.log_event(db, "exit_failed", "", bar, &error.to_string())
                        .await;
                } else if is_transient(&error) {
                    // A database hiccup says nothing about whether the book is
                    // trustworthy, so it must not retire the strategy for the
                    // session. The strategy has taken the discard and will
                    // re-signal if it still wants the trade.
                    tracing::warn!(
                        %error,
                        account_strategy_id = self.target.account_strategy_id,
                        strategy = self.target.strategy,
                        "live entry failed on a transient error; skipped"
                    );
                    self.log_event(db, "transient_skip", "", bar, &error.to_string())
                        .await;
                } else {
                    self.blocked = Some(BlockReason::Sticky);
                    tracing::error!(
                        %error,
                        account_strategy_id = self.target.account_strategy_id,
                        strategy = self.target.strategy,
                        "live action failed; strategy blocked"
                    );
                    // The one event that matters most: this is the state that
                    // silently cost ofi_momentum six trades and 3.5 hours.
                    self.log_event(db, "blocked", "", bar, &error.to_string())
                        .await;
                }
            }
        }
        self.flatten_at_session_end(db, bar).await;
    }

    /// Whether `action` is a strategy exit for a position the session-end
    /// flatten has already taken away. The flatten does not — and cannot — tell
    /// the strategy, so a strategy that keeps its own book emits its exit a
    /// minute later anyway: `noise_momentum_2` holds its position past the
    /// flatten and closes at 15:30 every session. The engine absorbs that
    /// silently because its slot is already flat, and live has to as well, or a
    /// routine end-of-day exit reads as state desync and blocks the strategy
    /// for the rest of the run.
    pub(super) fn is_settled_close(&self, action: Action, bar: Bar) -> bool {
        let key = match action {
            Action::Close { .. } => "primary".to_owned(),
            Action::ClosePosition { id, .. } => format!("tag:{id}"),
            _ => return false,
        };
        !self.positions.contains_key(&key) && self.in_session_end_window(bar)
    }

    /// Whether `bar` falls in the window where the session-end flatten belongs:
    /// the bar the backtest engine would have flattened on (`session_end - 1`,
    /// the last bar before the close) and `SESSION_END_GRACE_MINUTES` after it.
    ///
    /// The grace exists because a feed gap over the exact minute would otherwise
    /// mean the flatten never fires. It is a WINDOW rather than the old
    /// open-ended `minute + 1 >= session_end` because that comparison is blind
    /// to sessions that straddle New York midnight: `index_book` reports
    /// `session_end` in real New York minutes, so AUS200 (session 19:00-02:00)
    /// reports 120 and HK50 (21:00-04:00) reports 240. Every bar of either
    /// session has a minute above 1100, so `minute + 1 >= 120` was true for the
    /// whole session and every entry was flattened on its own bar — the live
    /// AUS200 and HK50 sleeves could not hold a position for one minute.
    ///
    /// Measuring the offset forward from the flatten bar on the 1440-minute ring
    /// makes the test wrap correctly: AUS200's 21:00 bar is 1141 minutes past
    /// its 01:59 flatten bar, not one minute past it.
    pub(super) fn in_session_end_window(&self, bar: Bar) -> bool {
        let minute = bar.ts.rem_euclid(86_400) as usize / 60;
        self.strategy
            .session_end_minute()
            .is_some_and(|session_end| {
                let flatten_bar = (session_end + 1_440 - 1) % 1_440;
                (minute + 1_440 - flatten_bar) % 1_440 <= SESSION_END_GRACE_MINUTES
            })
    }

    /// Whether `minute` of the New York day falls inside this strategy's own
    /// trading session.
    ///
    /// WRAPPING IS THE POINT. JP225 trades 19:00-02:00 and therefore reports a
    /// start of 1140 and an end of 120, so the plain range test is false for
    /// every minute it actually trades. Only the feed watchdog reads this, and
    /// it fails OPEN -- a strategy that declares no session is treated as always
    /// in one, which is the old behaviour and the conservative answer, because
    /// the cost of a wrong `false` is an unmanaged position and the cost of a
    /// wrong `true` is one unnecessary flatten.
    pub(super) fn in_trading_session(&self, minute: i64) -> bool {
        let (Some(start), Some(end)) = (
            self.strategy.session_start_minute(),
            self.strategy.session_end_minute(),
        ) else {
            return true;
        };
        let (start, end) = (start as i64, end as i64);
        if start <= end {
            (start..=end).contains(&minute)
        } else {
            minute >= start || minute <= end
        }
    }

    /// Whether this slot's broker leg must be flattened on `bar`.
    ///
    /// `flattens_itself()` strategies emit their own session-close exit, at the
    /// close candle's OPEN, and running the generic flatten on top of them takes
    /// that exit away — the backtest bug that made XALUSD PDR read -2.08%
    /// against Python's +31.17%. `engine.rs` has honoured the opt-in since that
    /// fix; live never did, so the double flatten stayed live-only. Skipping
    /// them here is safe because they close the position themselves, and the
    /// `pending_exits` retry already covers a close that fails to reach MT5.
    pub(super) fn at_session_end(&self, bar: Bar) -> bool {
        !self.strategy.flattens_itself() && self.in_session_end_window(bar)
    }

    /// Whether this slot is holding one bar past its own session close, and so
    /// must be flattened whether or not its market is printing.
    ///
    /// ASKED OF A CLOCK, NOT OF A BAR, WHICH IS THE WHOLE POINT. Every other
    /// exit in this runtime -- the strategy's own, `in_session_end_window`, the
    /// stop -- is driven by a bar ARRIVING on the market being exited, so a
    /// vendor that stops publishing silences all of them together and the broker
    /// leg is left open with nobody managing it. That held two USDJPY sleeves
    /// nearly six hours past a 13:00 close on 2026-09-07. `ny_second_of_day`
    /// runs off the host clock and the learned offset, so it keeps answering
    /// through a total blackout.
    ///
    /// ONE BAR, WHICH IS WHERE THE RESEARCH MODEL ALREADY PUTS THE EXIT.
    /// `exness_live_execution` prices a session flatten at `bar_seconds + lag`
    /// past the close -- 13:30:01.6 for a 13:00 session -- unconditionally,
    /// whatever the feed did. So firing here is not a late backstop racing the
    /// strategy; it is the same instant the study assumes, and a strategy exit
    /// that arrives later than this because the feed went sparse is ITSELF the
    /// divergence. Whichever closes first wins and both agree on the moment.
    ///
    /// The grace is seconds, not minutes, and exists only so a healthy feed's
    /// own exit -- which lands about three seconds in -- is not raced into a
    /// duplicate close.
    ///
    /// The session test is what keeps this from firing on a perfectly good
    /// position: inside its own session a slot is never overdue, and the clock
    /// is a ring, so "past the close" would otherwise read true for most of the
    /// day.
    pub(super) fn session_overdue(&self, second_of_day: i64) -> bool {
        if self.positions.is_empty() || self.in_trading_session(second_of_day / 60) {
            return false;
        }
        // ALREADY OWED IS ALREADY HANDLED, and without this the close repeats
        // at the poll rate. `forced_flatten` deliberately KEEPS the position --
        // the broker has not acknowledged yet and the owed-exit loop must be
        // able to retry -- so `positions` stays non-empty and every one of the
        // next ticks finds the same slot overdue again. On 2026-09-09
        // `ethusd_pullback` logged 250 `session_end_overdue` flattens between
        // 20:30:10 and 20:30:59, five a second, until the acknowledgement
        // landed at 20:31:00. Only one close reached the broker -- the second
        // `enqueue_live_close` found the durable row already `pending_close`
        // and returned `None` -- so this was 250 wasted writes and an alarm
        // that reads like a runaway, not 250 orders. It is still wrong, and the
        // pending exit is the exact evidence that the work is in flight.
        if self
            .positions
            .keys()
            .all(|key| self.pending_exits.contains_key(key))
        {
            return false;
        }
        self.strategy
            .session_end_minute()
            .is_some_and(|session_end| {
                let past = (second_of_day - session_end as i64 * 60).rem_euclid(86_400);
                past >= SESSION_OVERDUE_MINUTES * 60 + SESSION_OVERDUE_GRACE_SECONDS
            })
    }

    /// The backtest engine flattens on the last bar before `session_end_minute`
    /// and every strategy relies on that: past its exit minute a strategy simply
    /// drops the position from its own books and emits nothing. Live had no
    /// equivalent, so the broker leg was orphaned and stayed open past 16:00
    /// New York until the process shut down. This closes it on the same bar the
    /// engine would have.
    pub(super) async fn flatten_at_session_end(&mut self, db: &Database, bar: Bar) {
        if self.positions.is_empty() || !self.at_session_end(bar) {
            return;
        }
        let minute = bar.ts.rem_euclid(86_400) as usize / 60;
        for key in self.positions.keys().cloned().collect::<Vec<_>>() {
            match self
                .close(db, &key, bar.close, 1.0, bar, "session_end")
                .await
            {
                Ok(()) => tracing::info!(
                    account_strategy_id = self.target.account_strategy_id,
                    strategy = self.target.strategy,
                    position_key = key,
                    minute,
                    session_end = self.strategy.session_end_minute(),
                    "flattened live position at session end"
                ),
                Err(error) => {
                    // Owed rather than blocked, same as a strategy exit: the
                    // next bar retries. Blocking here would guarantee the very
                    // overnight position this flatten exists to prevent.
                    self.pending_exits.insert(
                        key.clone(),
                        PendingExit {
                            price: bar.close,
                            fraction: 1.0,
                        },
                    );
                    tracing::error!(
                        %error,
                        position_key = key,
                        "session-end flatten failed; queued for retry"
                    );
                }
            }
        }
    }

    /// Durable counterpart to the tracing calls beside it. `bar` supplies the
    /// market minute the decision belongs to, which is what makes an event
    /// line up with a trade after the fact.
    pub(super) async fn log_event(
        &self,
        db: &Database,
        kind: &str,
        key: &str,
        bar: Bar,
        detail: &str,
    ) {
        db.log_live_event(
            self.target.account_strategy_id,
            &self.target.strategy,
            kind,
            key,
            &format_ts(self.decision_timestamp(bar)),
            detail,
        )
        .await;
    }

    /// The strategy's quantity as an MT5 lot volume.
    ///
    /// Every path that either sends a volume or records one must go through
    /// here: `open`, warm-up replay, and the broker-minimum check. Reconciliation
    /// hydrates the filled durable volume after it verifies key and side.
    pub(super) fn to_lots(&self, quantity: f64) -> f64 {
        to_lot_volume(&self.target.strategy, quantity)
    }

    pub(super) fn decision_timestamp(&self, bar: Bar) -> i64 {
        bar.ts + market_step(strategy_symbol(&self.target.strategy))
    }

    /// Why this entry must not be sent, or `None` to send it.
    ///
    /// There is deliberately no upper volume cap. One used to exist
    /// (`MT5_MAX_LIVE_VOLUME`, default one lot) and it was a trap: sizing is a
    /// fraction of equity, so the cap binds only once the account has grown,
    /// and when it does it refuses *entries* while still allowing exits -- a
    /// book that quietly stops opening and keeps closing. Position size is the
    /// strategies' business and the broker's; it is not this function's.
    pub(super) fn entry_refusal(
        &self,
        action: Action,
        entries_current: bool,
    ) -> Option<&'static str> {
        let quantity = match action {
            Action::Enter { quantity, .. } | Action::EnterPosition { quantity, .. } => quantity,
            _ => return None,
        };
        let volume = self.to_lots(quantity);
        if !entries_current {
            Some("signal belongs to a stale catch-up bar")
        } else if !self.target.connected {
            Some("MT5 bridge is offline")
        } else if self.target.equity <= 0.0 {
            Some("MT5 equity is unavailable")
        } else if !volume.is_finite() || volume < lot_spec(&self.target.strategy).min_lots {
            Some("strategy volume is below the broker minimum")
        } else {
            None
        }
    }

    pub(super) async fn execute(
        &mut self,
        db: &Database,
        action: Action,
        bar: Bar,
    ) -> Result<(), crate::error::ApiError> {
        match action {
            Action::Hold => Ok(()),
            Action::Enter { .. } => self.open(db, "primary", action, bar).await,
            Action::Close { price, fraction } => {
                self.close(db, "primary", price, fraction, bar, "strategy")
                    .await
            }
            Action::EnterPosition { id, .. } => {
                self.open(db, &format!("tag:{id}"), action, bar).await
            }
            Action::ClosePosition { id, price } => {
                self.close(db, &format!("tag:{id}"), price, 1.0, bar, "strategy")
                    .await
            }
        }
    }

    /// `action` must be an entry; `execute` is the only caller and never passes
    /// anything else.
    pub(super) async fn open(
        &mut self,
        db: &Database,
        key: &str,
        action: Action,
        bar: Bar,
    ) -> Result<(), crate::error::ApiError> {
        let (side, price, volume) = match action {
            Action::Enter {
                side,
                price,
                quantity,
            }
            | Action::EnterPosition {
                side,
                price,
                quantity,
                ..
            } => (side, price, quantity),
            _ => {
                return Err(crate::error::ApiError::Internal(
                    "open was handed an action that is not an entry".into(),
                ));
            }
        };
        if !self.target.connected {
            return Err(crate::error::ApiError::BadRequest(
                "MT5 bridge is offline; entry refused".into(),
            ));
        }
        if self.target.equity <= 0.0 {
            return Err(crate::error::ApiError::BadRequest(
                "MT5 equity is unavailable; entry refused".into(),
            ));
        }
        // The strategy speaks its own unit; MT5 only speaks lots.
        let spec = lot_spec(&self.target.strategy);
        let volume = self.to_lots(volume);
        // Only the broker's floor, and it is per-symbol -- ETHUSD's is 0.10, not
        // 0.01. There is no ceiling by design; see `entry_refusal`.
        if !volume.is_finite() || volume < spec.min_lots {
            return Err(crate::error::ApiError::BadRequest(format!(
                "strategy volume {volume} is below the broker minimum {}",
                spec.min_lots
            )));
        }
        if self.positions.contains_key(key) {
            if self.pending_exits.contains_key(key) {
                self.deferred_entries.insert(
                    key.to_owned(),
                    DeferredEntry {
                        action,
                        signal_bar: bar,
                    },
                );
                return Ok(());
            }
            return Err(crate::error::ApiError::BadRequest(
                "strategy attempted to reuse an open position key".into(),
            ));
        }
        let symbol = route_symbol(&self.target.symbol)?;
        let command_id = db
            .enqueue_live_open(
                self.target.account_strategy_id,
                self.target.account_id,
                self.target.environment_id,
                &self.target.strategy,
                key,
                side_name(side),
                symbol,
                volume,
                &format_ts(self.decision_timestamp(bar)),
                price,
            )
            .await?;
        self.positions
            .insert(key.to_owned(), LogicalPosition { side, volume });
        self.pending_opens
            .insert(key.to_owned(), PendingOpen { command_id, action });
        Ok(())
    }

    pub(super) async fn close(
        &mut self,
        db: &Database,
        key: &str,
        price: f64,
        fraction: f64,
        bar: Bar,
        exit_reason: &str,
    ) -> Result<(), crate::error::ApiError> {
        let Some(_) = self.positions.get(key) else {
            return Err(crate::error::ApiError::BadRequest(format!(
                "strategy attempted to close unknown position {key}"
            )));
        };
        let symbol = route_symbol(&self.target.symbol)?;
        let enqueued = db
            .enqueue_live_close(&LiveCloseRequest {
                account_strategy_id: self.target.account_strategy_id,
                account_id: self.target.account_id,
                strategy: &self.target.strategy,
                position_key: key,
                symbol,
                fraction,
                timestamp: &format_ts(self.decision_timestamp(bar)),
                price,
                exit_reason,
            })
            .await?;
        if enqueued.is_none() {
            // No row in ('pending_open','open'). Either a close is already in
            // flight or the position is not ours any more; both mean the broker
            // leg is settled and there is nothing left to send. Neither is a
            // reason to block — the old code treated both as desync, which is
            // what stopped ofi_momentum for the rest of the session.
            let status = db
                .strategy_positions(self.target.account_strategy_id)
                .await?
                .into_iter()
                .find(|position| position.position_key == key)
                .map(|position| position.status);
            if status.is_some() {
                // A close is in flight or its failed result is being committed.
                // Retain ownership until durable state proves the broker leg is
                // gone; queueing alone is never settlement.
                self.pending_exits
                    .insert(key.to_owned(), PendingExit { price, fraction });
            } else {
                self.positions.remove(key);
                self.pending_opens.remove(key);
                self.pending_exits.remove(key);
            }
            return Ok(());
        }
        self.pending_exits
            .insert(key.to_owned(), PendingExit { price, fraction });
        Ok(())
    }

    /// Commits or rolls back entries after the asynchronous MT5 result arrives.
    /// The strategy has already advanced when it emits an action, so a failed
    /// command must be explicitly discarded; otherwise it suppresses future
    /// signals for a position the broker never opened.
    pub(super) async fn settle_pending_opens(&mut self, db: &Database, bar: Bar) {
        for (key, pending) in self
            .pending_opens
            .iter()
            .map(|(key, pending)| (key.clone(), *pending))
            .collect::<Vec<_>>()
        {
            match db.mt5_command_status(pending.command_id).await {
                Ok(Some(status)) if status == "filled" => {
                    self.pending_opens.remove(&key);
                    self.log_event(db, "entry_acknowledged", &key, bar, "")
                        .await;
                }
                Ok(Some(status)) if matches!(status.as_str(), "failed" | "superseded") => {
                    self.strategy.discard(pending.action);
                    self.positions.remove(&key);
                    self.pending_opens.remove(&key);
                    self.pending_exits.remove(&key);
                    self.deferred_entries.remove(&key);
                    tracing::error!(
                        command_id = pending.command_id,
                        account_strategy_id = self.target.account_strategy_id,
                        strategy = self.target.strategy,
                        position_key = key,
                        %status,
                        "MT5 rejected live entry; strategy state rolled back"
                    );
                    self.log_event(db, "entry_rejected", &key, bar, &status)
                        .await;
                }
                Ok(Some(_)) => {}
                Ok(None) => {
                    self.strategy.discard(pending.action);
                    self.positions.remove(&key);
                    self.pending_opens.remove(&key);
                    self.pending_exits.remove(&key);
                    self.deferred_entries.remove(&key);
                    self.blocked = Some(BlockReason::Sticky);
                    self.log_event(
                        db,
                        "blocked",
                        &key,
                        bar,
                        "pending MT5 entry command disappeared",
                    )
                    .await;
                }
                Err(error) => tracing::warn!(
                    %error,
                    command_id = pending.command_id,
                    "unable to settle pending MT5 entry"
                ),
            }
        }
    }

    /// Re-sends exits that failed to reach or fill at the broker. Runs before the
    /// strategy's own actions so an owed exit always goes out on the next bar
    /// rather than waiting for a strategy that has already forgotten it.
    pub(super) async fn retry_pending_exits(&mut self, db: &Database, bar: Bar) {
        if self.pending_exits.is_empty() {
            return;
        }
        for (key, exit) in self
            .pending_exits
            .iter()
            .map(|(key, exit)| (key.clone(), *exit))
            .collect::<Vec<_>>()
        {
            let Some(logical) = self.positions.get(&key).cloned() else {
                self.pending_exits.remove(&key);
                continue;
            };
            let durable = match db.strategy_positions(self.target.account_strategy_id).await {
                Ok(positions) => positions
                    .into_iter()
                    .find(|position| position.position_key == key),
                Err(error) => {
                    tracing::warn!(%error, position_key = key, "unable to inspect owed exit");
                    continue;
                }
            };
            let Some(durable) = durable else {
                self.positions.remove(&key);
                self.pending_opens.remove(&key);
                self.pending_exits.remove(&key);
                self.log_event(db, "exit_acknowledged", &key, bar, "").await;
                if let Some(deferred) = self.deferred_entries.remove(&key) {
                    if bar.ts.saturating_sub(deferred.signal_bar.ts)
                        <= market_step(strategy_symbol(&self.target.strategy))
                    {
                        if let Err(error) = self.execute(db, deferred.action, bar).await {
                            self.strategy.discard(deferred.action);
                            // ONLY EVER SETS, NEVER CLEARS. This was an
                            // assignment -- `= !is_transient(&error)` -- so a
                            // database hiccup on a deferred entry silently
                            // UNBLOCKED a sleeve a feed watchdog had stopped for
                            // a real reason. A transient failure says nothing
                            // about whether the book is trustworthy, which is
                            // exactly why it must not answer the question.
                            if !is_transient(&error) {
                                self.blocked = Some(BlockReason::Sticky);
                            }
                            self.log_event(
                                db,
                                "deferred_entry_failed",
                                &key,
                                bar,
                                &error.to_string(),
                            )
                            .await;
                        }
                    } else {
                        self.strategy.discard(deferred.action);
                        self.log_event(db, "deferred_entry_expired", &key, bar, "")
                            .await;
                    }
                }
                continue;
            };
            if durable.status == "pending_close" {
                continue;
            }
            // A partial close is acknowledged by its durable remaining volume.
            // Do not send the same fraction a second time.
            if durable.remaining_volume + 1e-9 < logical.volume {
                if durable.remaining_volume <= 1e-9 {
                    self.positions.remove(&key);
                } else if let Some(position) = self.positions.get_mut(&key) {
                    position.volume = durable.remaining_volume;
                }
                self.pending_exits.remove(&key);
                self.log_event(db, "partial_exit_acknowledged", &key, bar, "")
                    .await;
                continue;
            }
            match self
                .close(db, &key, exit.price, exit.fraction, bar, "retry")
                .await
            {
                Ok(()) => {
                    tracing::info!(
                        account_strategy_id = self.target.account_strategy_id,
                        strategy = self.target.strategy,
                        position_key = key,
                        "owed exit re-sent"
                    );
                    self.log_event(db, "exit_retry_ok", &key, bar, "").await;
                }
                Err(error) => {
                    tracing::error!(
                        %error,
                        position_key = key,
                        "owed exit failed again; will retry on the next bar"
                    );
                    self.log_event(db, "exit_retry_failed", &key, bar, &error.to_string())
                        .await;
                }
            }
        }
    }

    /// Records an exit that could not be sent so `retry_pending_exits` owns it
    /// from here. The strategy will not ask again.
    pub(super) fn owe_exit(&mut self, action: Action) {
        let (key, price, fraction) = match action {
            Action::Close { price, fraction } => ("primary".to_owned(), price, fraction),
            Action::ClosePosition { id, price } => (format!("tag:{id}"), price, 1.0),
            _ => return,
        };
        if self.positions.contains_key(&key) {
            self.pending_exits
                .insert(key, PendingExit { price, fraction });
        }
    }

    /// Warm-up replay's mirror of `open`/`close`. Volumes are kept in lots and
    /// replaced by the durable filled volume when an open position reconciles.
    pub(super) fn apply_expected(&mut self, action: Action) {
        match action {
            Action::Hold => {}
            Action::Enter { side, quantity, .. } => {
                self.positions.insert(
                    "primary".into(),
                    LogicalPosition {
                        side,
                        volume: self.to_lots(quantity),
                    },
                );
            }
            Action::Close { fraction, .. } => {
                if fraction >= 1.0 - 1e-9 {
                    self.positions.remove("primary");
                } else if let Some(position) = self.positions.get_mut("primary") {
                    position.volume *= 1.0 - fraction;
                }
            }
            Action::EnterPosition {
                id, side, quantity, ..
            } => {
                self.positions.insert(
                    format!("tag:{id}"),
                    LogicalPosition {
                        side,
                        volume: self.to_lots(quantity),
                    },
                );
            }
            Action::ClosePosition { id, .. } => {
                self.positions.remove(&format!("tag:{id}"));
            }
        }
    }

    /// Reconciles an in-memory position that has been closed externally (e.g.
    /// manually closed by the operator in MetaTrader 5 or via protective close).
    /// Resets the strategy engine's internal position state to flat so it can
    /// take the next signal cleanly, removes the position from in-memory tracking,
    /// and clears any ownership block.
    pub(super) fn reconcile_closed_position(&mut self, position_key: &str) {
        self.strategy.abandon_open_position();
        self.positions.remove(position_key);
        self.pending_opens.remove(position_key);
        self.pending_exits.remove(position_key);
        self.deferred_entries.remove(position_key);
        if self.blocked == Some(BlockReason::Ownership) {
            self.blocked = None;
        }
    }
}
