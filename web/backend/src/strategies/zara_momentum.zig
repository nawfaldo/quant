const std = @import("std");
const engine = @import("../bt/engine.zig");
const data = @import("../bt/data.zig");
const sizing = @import("../sizings/vol_target.zig");
const trade_filter = @import("trade_filter.zig");

// Hardcoded entry filters — listed days/hours are EXCLUDED from new entries;
// empty list ⇒ filter off (see trade_filter.zig).
// e.g. &.{ "Monday", "Tuesday" } / &.{ "09.00", "12.00" }
const filter_day: []const []const u8 = &.{};
const filter_hour: []const []const u8 = &.{"12.00"};

// When a filtered day/hour arrives with a trade still open: true ⇒ close it
// immediately on that bar, false ⇒ keep holding it (normal exits still apply).
const filter_close_open: bool = false;

// All tunable defaults live in zara_momentum_config.zig — edit there, not here.
const cfg = @import("zara_momentum_config.zig").config;

// ── Intraday Momentum (Noise Area) ───────────────────────────────────────────
//
// Zarattini, Aziz & Barbon, "Beat the Market: An Effective Intraday Momentum
// Strategy for S&P500 ETF (SPY)" (SSRN 24-97, 2024) — the paper's headline
// variant: trailing stop at max(current band, VWAP), everything flat by 16:00.
//
// Noise Area — a time-of-day-dependent band around the day's open inside which
// price action is treated as noise. For each half-hour slot HH:MM:
//
//   σ(HH:MM)  = average over the previous `lookback` (14) days of
//               |Close(d, HH:MM) / Open(d, 09:30) − 1|
//   UpperBound = max(Open_today, Close_yesterday) × (1 + VM·σ)
//   LowerBound = min(Open_today, Close_yesterday) × (1 − VM·σ)
//
// (The max/min against yesterday's 16:00 close widens the band on the gapped
// side so an overnight gap alone doesn't count as an intraday imbalance.)
//
// Rules (checked once per 30m bar; a 30m bar stamped HH:MM closes at HH:MM+30,
// and the engine fills at the next bar's open — i.e. decisions made on the
// price at each half-hour mark execute at that same half-hour mark, matching
// the paper's semi-hourly execution):
//   • First decision at 10:00 (close of the 09:30 bar); last entry decision at
//     15:30 (close of the 15:00 bar).
//   • Close above UB → long. Close below LB → short. Crossing to the opposite
//     boundary while in a position flips it.
//   • Trailing stop (the paper's best variant): long exits when the close is
//     no longer above max(UB, sessionVWAP); short exits when it is no longer
//     below min(LB, sessionVWAP). Session VWAP uses RTH bars only, typical
//     price (H+L+C)/3, reset each day, current bar folded in before comparing.
//   • EOD: the 15:30 bar (which closes at 16:00) flattens any position via
//     `exit_fill` at that bar's close — the exact 16:00 price.
//   • A slot only trades once its σ history holds `lookback` full days; the
//     engine's warm-up fetch (90 calendar days) primes this before the eval
//     window starts.
//
// Params (paper defaults, fixed): VM = 1.0, lookback = 14 days.
//
// ── Position sizing ───────────────────────────────────────────────────────────
//   Same surface as the other strategies: `sizing_mode` selects fixed lots
//   (.none), EWMA vol targeting (.vol_target), or the paper's own daily-vol
//   sizing (.paper_vol — min(4, 2%/σ₁₄), see sizings/paper_vol.zig). The size
//   is set on `self.contracts` just before each entry/flip.

pub const ZaraMomentum = struct {
    pub const timeframe: []const u8 = "30m";
    pub const columns = .{
        .open = true,
        .high = true,
        .low = true,
        .close = true,
        .volume = true,
    };

    initial_balance: f64 = cfg.initial_balance,
    contracts: f64 = cfg.contracts,
    leverage: f64 = cfg.leverage,

    // Position sizing (see header).
    sizing_mode: sizing.Mode = cfg.sizing_mode,
    vol: sizing.VolTarget = cfg.vol,

    // Strategy params (see zara_momentum_config.zig).
    vm: f64 = cfg.vm, // Volatility Multiplier — noise-area width scaler
    lookback: u32 = cfg.lookback, // days averaged into each slot's σ (≤ MAX_LOOKBACK)

    // Engine hook: when non-null the engine closes at exactly this price on the
    // CURRENT bar (see engine.closeFill). Used for the 16:00 flatten so the
    // exit lands on the exact closing price even if the next bar gaps.
    exit_fill: ?f64 = null,

    // ── Session state ──
    current_day: [10]u8 = .{0} ** 10,
    day_open: f64 = 0.0, // today's 09:30 open (0 = not seen → no trading today)
    prev_close: f64 = 0.0, // yesterday's last RTH close (16:00), 0 until known
    day_last_rth_close: f64 = 0.0, // running last RTH close of the current day
    cum_pv: f64 = 0.0, // Σ typical × volume (RTH session VWAP)
    cum_vol: f64 = 0.0, // Σ volume

    // Per-slot σ history: ring buffer of |move from open| per decision slot.
    moves: [SLOTS][MAX_LOOKBACK]f64 = .{.{0.0} ** MAX_LOOKBACK} ** SLOTS,
    counts: [SLOTS]u32 = .{0} ** SLOTS,
    heads: [SLOTS]u32 = .{0} ** SLOTS,

    // Mirror of the engine's position, driven by the signals we emit. Safe
    // because fills always succeed (next bar open) and every day starts flat.
    pos: enum { flat, long, short } = .flat,

    // Baseline lots (base_lots × leverage), snapshotted once on the first bar.
    base_contracts: f64 = 0.0,
    base_set: bool = false,

    // Latest computed boundaries for indicators/monitoring.
    ub: f64 = 0.0,
    lb: f64 = 0.0,

    const SLOTS: usize = 12; // bars stamped 09:30..15:00 → decisions 10:00..15:30
    const MAX_LOOKBACK: usize = 64;
    const RTH_OPEN: u16 = 9 * 60 + 30; // 09:30 — first RTH bar
    const EOD_BAR: u16 = 15 * 60 + 30; // 15:30 bar closes at 16:00 → flatten

    pub fn update(self: *ZaraMomentum, bar: engine.Bar, ts: data.Ts) engine.Signal {
        if (!self.base_set) {
            self.base_contracts = self.contracts;
            self.base_set = true;
        }

        const hh = std.fmt.parseInt(u8, ts[11..13], 10) catch {
            self.ub = 0.0;
            self.lb = 0.0;
            return .flat;
        };
        const mm = std.fmt.parseInt(u8, ts[14..16], 10) catch {
            self.ub = 0.0;
            self.lb = 0.0;
            return .flat;
        };
        const mins: u16 = @as(u16, hh) * 60 + mm;

        const day_changed = !std.mem.eql(u8, ts[0..10], self.current_day[0..]);

        // Feed the active vol sizer every bar so it tracks the running daily
        // close and folds completed-day returns in on the rollover.
        switch (self.sizing_mode) {
            .vol_target => self.vol.onBar(bar.close, day_changed),
            .none => {},
        }

        if (day_changed) {
            @memcpy(&self.current_day, ts[0..10]);
            // Freeze yesterday's final RTH close as the gap reference. Days
            // with no RTH bars (weekends/holidays) leave the older value.
            if (self.day_last_rth_close > 0.0) self.prev_close = self.day_last_rth_close;
            self.day_last_rth_close = 0.0;
            self.day_open = 0.0;
            self.cum_pv = 0.0;
            self.cum_vol = 0.0;
        }

        // Outside the decision window (overnight bars, and anything at/after
        // 16:00). Positions are normally flat by here; if the 15:30 bar was
        // missing (early close / data gap), flatten on the first chance.
        if (mins < RTH_OPEN or mins > EOD_BAR) {
            self.ub = 0.0;
            self.lb = 0.0;
            if (self.pos != .flat) {
                self.pos = .flat;
                return .close;
            }
            return .flat;
        }

        if (mins == RTH_OPEN) self.day_open = bar.open;
        self.day_last_rth_close = bar.close;

        // No 09:30 bar seen today → no open reference, no trading today.
        if (self.day_open <= 0.0) {
            self.ub = 0.0;
            self.lb = 0.0;
            if (self.pos != .flat) {
                self.pos = .flat;
                return .close;
            }
            return .flat;
        }

        // Session VWAP (RTH only): fold the current bar in before comparing.
        const typical = (bar.high + bar.low + bar.close) / 3.0;
        const v = @as(f64, @floatFromInt(bar.volume));
        self.cum_pv += typical * v;
        self.cum_vol += v;
        const vwap = if (self.cum_vol > 0.0) self.cum_pv / self.cum_vol else bar.close;

        // 15:30 bar closes at 16:00 — flatten at that exact price, no entries.
        // ub/lb are deliberately left as-is (last slot's band, computed at the
        // 15:00 bar) instead of zeroed here: the band is still in effect until
        // the 16:00 flatten, and indicator consumers rely on ub/lb staying
        // non-zero through the session's final bar (see bt/run.zig).
        if (mins == EOD_BAR) {
            if (self.pos != .flat) {
                self.pos = .flat;
                self.exit_fill = bar.close;
                return .close;
            }
            return .flat;
        }

        const slot: usize = (mins - RTH_OPEN) / 30; // 0..SLOTS-1

        // Boundaries from the PREVIOUS `lookback` days (today's move is pushed
        // after the decision, below, so the band never sees the current day).
        const win: u32 = @intCast(@max(1, @min(self.lookback, MAX_LOOKBACK)));
        var sig: engine.Signal = .flat;
        if (self.counts[slot] >= win) {
            var sum: f64 = 0.0;
            for (self.moves[slot][0..win]) |m| sum += m;
            const sigma = sum / @as(f64, @floatFromInt(win));

            const ref = if (self.prev_close > 0.0) self.prev_close else self.day_open;
            const ub = @max(self.day_open, ref) * (1.0 + self.vm * sigma);
            const lb = @min(self.day_open, ref) * (1.0 - self.vm * sigma);
            self.ub = ub;
            self.lb = lb;
            const c = bar.close;

            // Entry filters gate new entries and flips only; the trailing
            // stop and the 16:00 flatten are never filtered. With
            // filter_close_open a filtered bar also flattens an open trade
            // immediately instead of waiting for a normal exit.
            const entry_ok = trade_filter.allowed(filter_day, filter_hour, ts);
            if (!entry_ok and filter_close_open and self.pos != .flat) {
                self.pos = .flat;
                sig = .close;
            } else switch (self.pos) {
                .flat => {
                    if (c > ub and entry_ok) {
                        sig = self.enter(.long);
                    } else if (c < lb and entry_ok) {
                        sig = self.enter(.short);
                    }
                },
                .long => {
                    if (c < lb and entry_ok) {
                        sig = self.enter(.short); // crossover → flip
                    } else if (c <= @max(ub, vwap)) {
                        self.pos = .flat; // trailing stop: back inside band or under VWAP
                        sig = .close;
                    } else {
                        sig = .long; // hold — engine ignores same-side repeats
                    }
                },
                .short => {
                    if (c > ub and entry_ok) {
                        sig = self.enter(.long);
                    } else if (c >= @min(lb, vwap)) {
                        self.pos = .flat;
                        sig = .close;
                    } else {
                        sig = .short;
                    }
                },
            }
        } else {
            self.ub = 0.0;
            self.lb = 0.0;
        }

        // Record today's move for this slot (AFTER the decision — no lookahead).
        const move = @abs(bar.close / self.day_open - 1.0);
        self.moves[slot][self.heads[slot]] = move;
        self.heads[slot] = (self.heads[slot] + 1) % win;
        if (self.counts[slot] < win) self.counts[slot] += 1;

        return sig;
    }

    fn enter(self: *ZaraMomentum, side: enum { long, short }) engine.Signal {
        const mult = switch (self.sizing_mode) {
            .none => 1.0,
            .vol_target => self.vol.multiplier(),
        };
        self.contracts = self.base_contracts * mult;
        switch (side) {
            .long => {
                self.pos = .long;
                return .long;
            },
            .short => {
                self.pos = .short;
                return .short;
            },
        }
    }
};
