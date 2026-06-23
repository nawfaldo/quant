const std = @import("std");
const posix = std.posix;
const engine = @import("engine.zig");
const strategy = @import("strategy.zig");
const report = @import("report.zig");
const db = @import("db.zig");
const tune = @import("tune.zig");
const combine = @import("combine.zig");
const montecarlo = @import("montecarlo.zig");

const STDIN = posix.STDIN_FILENO;
const STDOUT = posix.STDOUT_FILENO;

// ── Bar layout ────────────────────────────────────────────────────────────────
//   row N-(COMMANDS.len+4) … N-5   one suggestion row per command
//   row N-4  ─── top separator ───
//   row N-3  > [input]  or  running indicator
//   row N-2  ─── bottom separator ───
//   row N-1  (empty)
//   row N    (empty)
const BAR_HEIGHT: usize = COMMANDS.len + 5;

var term_rows: usize = 24;
var term_cols: usize = 80;

// ── Commands ──────────────────────────────────────────────────────────────────

const Command = struct { name: []const u8, description: []const u8 };

const COMMANDS = [_]Command{
    .{ .name = "/run", .description = "run backtest" },
    .{ .name = "/tune", .description = "grid-search strategy parameters" },
    .{ .name = "/delete", .description = "delete a saved backtest" },
    .{ .name = "/combine", .description = "run several saved configs as one portfolio" },
    .{ .name = "/montecarlo", .description = "resample a saved backtest's trades" },
    .{ .name = "/exit", .description = "exit" },
};

fn isExactCommand(input: []const u8) bool {
    for (&COMMANDS) |cmd| {
        if (std.mem.eql(u8, cmd.name, input)) return true;
    }
    return false;
}

// ── Strategies ────────────────────────────────────────────────────────────────

const STRATEGIES = [_][]const u8{ "30M_BUY", "BUY_HOLD", "RTH_VWAP" };
const STRAT_ORB = 1;
const STRAT_BUYHOLD = 2;
const STRAT_VWAP = 3;

// ── Symbols ───────────────────────────────────────────────────────────────────

const SYMBOL_LABELS = [_][]const u8{ "NQ", "GBPUSD", "EURUSD" };
const SYMBOL_PREFIXES = [_][]const u8{ "nq", "gbpusd", "eurusd" };

// NQ price data can be traded three different ways; the CLI prompts for this
// only when NQ is selected (all other symbols are always forex). Index matches
// the menu order in INSTRUMENT_Q.
const INSTRUMENT_LABELS = [_][]const u8{ "forex", "nq mini", "nq micro" };
const INSTRUMENTS = [_]engine.Instrument{ .forex, .nq_mini, .nq_micro };
const INSTRUMENT_Q = "  Instrument?  1. forex  2. nq mini  3. nq micro";

// NQ is the only symbol that offers a choice of instrument; every other symbol
// is always a forex CFD. Used to gate the extra "Instrument?" question.
fn nqSelected() bool {
    return std.mem.eql(u8, SYMBOL_PREFIXES[g_symbol_idx], "nq");
}

// Futures instruments use whole "contracts"; forex uses fractional "lots".
// Drives the size terminology in the prompts (mirrors engine.usesContracts).
fn usesContracts() bool {
    return engine.usesContracts();
}

// ── CLI state ─────────────────────────────────────────────────────────────────

const State = enum {
    idle,
    awaiting_strategy,
    awaiting_symbol,
    awaiting_instrument,
    awaiting_balance,
    awaiting_base_contracts,
    awaiting_leverage,
    awaiting_sizing,
    awaiting_vol_target,
    awaiting_vol_halflife,
    awaiting_vol_maxmult,
    awaiting_vol_mindays,
    awaiting_from,
    awaiting_spread,
    awaiting_slippage,
    // /run flow — Buy & Hold (its own questions: balance, lots, date)
    awaiting_bh_balance,
    awaiting_bh_lots,
    awaiting_bh_from,
    awaiting_bh_spread,
    awaiting_bh_slippage,
    // /tune flow
    awaiting_tune_strategy,
    awaiting_tune_symbol,
    awaiting_tune_instrument,
    awaiting_tune_balance,
    awaiting_tune_base_contracts,
    awaiting_tune_leverage,
    awaiting_tune_sizing,
    awaiting_tune_vol_target,
    awaiting_tune_vol_halflife,
    awaiting_tune_vol_maxmult,
    awaiting_tune_vol_mindays,
    awaiting_tune_from,
    awaiting_tune_spread,
    awaiting_tune_slippage,
    awaiting_delete,
    // /combine flow
    awaiting_combine_balance,
    awaiting_combine_pick,
    // /montecarlo flow
    awaiting_mc_pick,
};

var g_running: bool = false;
var g_strategy_id: usize = 1; // 1 = 30M_BUY
var g_strategy_sel: [16]u8 = undefined;
var g_strategy_sel_len: usize = 0;
var g_symbol_idx: usize = 0; // index into SYMBOL_LABELS / SYMBOL_PREFIXES

var g_balance: f64 = 0;
var g_base_contracts: f64 = 1;
var g_leverage: f64 = 1.0;
// Shared by /run and /tune (flows are sequential, never concurrent).
var g_sizing_mode: strategy.sizing.Mode = .none;
var g_vol: strategy.sizing.VolTarget = .{};
var g_from_buf: [32]u8 = undefined;
var g_from_len: usize = 0;
var g_to_buf: [32]u8 = undefined;
var g_to_len: usize = 0;

const MAX_GRID = 32;
var g_tune_balance: f64 = 0;
var g_tune_base_contracts: [MAX_GRID]f64 = undefined;
var g_tune_base_contracts_n: usize = 0;
// Every sizing input is a swept dimension in /tune (the grid is their full
// cartesian product). When sizing is .none the vol lists are filled with a
// single default value so they contribute one combo each.
var g_tune_leverage: [MAX_GRID]f64 = undefined;
var g_tune_leverage_n: usize = 0;
var g_tune_vol_target: [MAX_GRID]f64 = undefined;
var g_tune_vol_target_n: usize = 0;
var g_tune_vol_halflife: [MAX_GRID]f64 = undefined;
var g_tune_vol_halflife_n: usize = 0;
var g_tune_vol_maxmult: [MAX_GRID]f64 = undefined;
var g_tune_vol_maxmult_n: usize = 0;
var g_tune_vol_mindays: [MAX_GRID]u32 = undefined;
var g_tune_vol_mindays_n: usize = 0;

const MAX_DELETE_ENTRIES = 32;
var g_delete_entries: [MAX_DELETE_ENTRIES]db.BacktestEntry = undefined;
var g_delete_count: usize = 0;

// /combine: the listing reuses g_delete_entries/g_delete_count (the flows are
// never concurrent). g_combine_ids holds the ids picked so far.
const MAX_COMBINE = MAX_DELETE_ENTRIES;
var g_combine_balance: f64 = 0;
var g_combine_ids: [MAX_COMBINE]i64 = undefined;
var g_combine_count: usize = 0;
// The picked entries, snapshotted before the combine worker starts so its thread
// reads from stable storage (g_delete_entries may be re-listed meanwhile).
var g_combine_entries: [MAX_COMBINE]db.BacktestEntry = undefined;

// ── Raw mode ──────────────────────────────────────────────────────────────────

var original_termios: posix.termios = undefined;
var raw_termios: posix.termios = undefined;

fn enableRawMode() !void {
    original_termios = try posix.tcgetattr(STDIN);
    var raw = original_termios;
    raw.lflag.ECHO = false;
    raw.lflag.ICANON = false;
    // ISIG stays false for the entire session. Ctrl+C is byte 3 in the read
    // buffer, not a signal. This means Ctrl+C can never kill zig build or the
    // process — only /exit can exit.
    raw.lflag.ISIG = false;
    raw.cc[@intFromEnum(posix.V.MIN)] = 1;
    raw.cc[@intFromEnum(posix.V.TIME)] = 0;
    try posix.tcsetattr(STDIN, .FLUSH, raw);
    raw_termios = raw;
}

fn disableRawMode() void {
    posix.tcsetattr(STDIN, .FLUSH, original_termios) catch {};
}

// ── Output ────────────────────────────────────────────────────────────────────

fn out(s: []const u8) void {
    _ = std.c.write(STDOUT, s.ptr, s.len);
}

// ── Terminal size ─────────────────────────────────────────────────────────────

fn refreshTermSize() void {
    const TIOCGWINSZ: u64 = switch (@import("builtin").os.tag) {
        .macos, .ios => 0x40087468,
        .linux => 0x5413,
        else => return,
    };
    var ws = std.mem.zeroes(posix.winsize);
    if (std.c.ioctl(STDOUT, TIOCGWINSZ, @intFromPtr(&ws)) < 0 or ws.col == 0) {
        _ = std.c.ioctl(STDIN, TIOCGWINSZ, @intFromPtr(&ws));
    }
    if (ws.row > 0 and ws.col > 0) {
        term_rows = @max(ws.row, BAR_HEIGHT + 2);
        term_cols = ws.col;
    }
}

// ── Alternate screen + scroll region ─────────────────────────────────────────
// The bar is pinned to the bottom rows using a hardware scroll region: content
// occupies rows [1, term_rows - BAR_HEIGHT] and scrolls within that region, while
// the bar sits in the fixed rows below it and never moves — so scrolling the
// terminal does not shift the textfield. Content fills the region from the TOP
// downward; we keep the content cursor in the terminal's save slot (DECSC \x1b7 /
// DECRC \x1b8) so drawing the bar — which uses absolute positioning — never
// disturbs where the next line of output lands.

fn enterFullscreen() void {
    var buf: [128]u8 = undefined;
    var pos: usize = 0;
    const seq = "\x1b[?1049h\x1b[2J\x1b[H";
    @memcpy(buf[pos..][0..seq.len], seq);
    pos += seq.len;
    const s = std.fmt.bufPrint(buf[pos..], "\x1b[1;{d}r", .{term_rows - BAR_HEIGHT}) catch return;
    pos += s.len;
    out(buf[0..pos]);
    // Park the content cursor at the top of the content region and save it.
    out("\x1b[H\x1b7");
}

fn exitFullscreen() void {
    out("\x1b[r\x1b[?1049l");
}

// ── Content area ──────────────────────────────────────────────────────────────
// Output is written at the saved content cursor (top-down within the scroll
// region) and re-saved afterwards, so the next call resumes where this one left
// off regardless of any bar repaints in between.
fn printContent(text: []const u8) void {
    out("\x1b8"); // restore content cursor (DECRC)
    out(text);
    out("\x1b7"); // save content cursor (DECSC)
}

// ── Flow helpers (inline Q&A form) ────────────────────────────────────────────
// The content cursor (ESC7) is always parked at the END of the current question
// line — no trailing newline — so each answer can overwrite that exact line in
// place via ESC8 + \r + \x1b[2K before writing the answer and the next question.

// Print a gray command-header line (full-width bg), a blank line, then the
// first question. Saves content cursor at the end of the first question (no \n).
fn startFlow(cmd: []const u8, first_q: []const u8) void {
    var buf: [512]u8 = undefined;
    out("\x1b8");
    const s = std.fmt.bufPrint(&buf, "\x1b[100m\x1b[1m  > {s}\x1b[K\x1b[0m\n\n{s}", .{ cmd, first_q }) catch return;
    out(s);
    out("\x1b7");
}

// Overwrite the current question line with the answered text, then print the
// next question (no \n). Saves cursor at the end of the next question.
fn flowNext(answered: []const u8, next_q: []const u8) void {
    var buf: [512]u8 = undefined;
    out("\x1b8\r\x1b[2K");
    const s = std.fmt.bufPrint(&buf, "{s}\n{s}", .{ answered, next_q }) catch return;
    out(s);
    out("\x1b7");
}

// Overwrite the last question line with the answered text, then 2 blank lines.
// report.print() adds its own leading \n, so the visual gap before the report
// is exactly 2 blank lines. Saves cursor where the report will begin.
fn flowEnd(answered: []const u8) void {
    out("\x1b8\r\x1b[2K");
    out(answered);
    out("\n\n");
    out("\x1b7");
}

// Overwrite the current question line with an error/cancellation message.
// Saves cursor after the message so content returns cleanly to idle.
fn flowFail(msg: []const u8) void {
    out("\x1b8\r\x1b[2K");
    out(msg);
    out("\n\n");
    out("\x1b7");
}

// Build "Strategy?  1. 30M_BUY  2. OTHER" into buf and return the slice.
fn strategyQuestion(buf: []u8) []const u8 {
    var pos: usize = 0;
    const hdr = std.fmt.bufPrint(buf[pos..], "  Strategy?", .{}) catch return "  Strategy?";
    pos += hdr.len;
    for (STRATEGIES, 1..) |s, i| {
        const e = std.fmt.bufPrint(buf[pos..], "  {d}. {s}", .{ i, s }) catch break;
        pos += e.len;
    }
    return buf[0..pos];
}

// Build "Symbol?  1. NQ  2. GBPUSD" into buf and return the slice.
fn symbolQuestion(buf: []u8) []const u8 {
    var pos: usize = 0;
    const hdr = std.fmt.bufPrint(buf[pos..], "  Symbol?", .{}) catch return "  Symbol?";
    pos += hdr.len;
    for (SYMBOL_LABELS, 1..) |s, i| {
        const e = std.fmt.bufPrint(buf[pos..], "  {d}. {s}", .{ i, s }) catch break;
        pos += e.len;
    }
    return buf[0..pos];
}

// "Sizing?  1. none  2. vol target" — the position-sizing selector.
const SIZING_Q = "  Sizing?  1. none  2. vol target";

// Per-parameter vol-target prompts. Each shows its default so the user can just
// press Enter to accept it (like the date-range question).
const VOL_TARGET_Q = "  Vol target? (annualized vol, enter for 0.20) ";
const VOL_HALFLIFE_Q = "  Vol halflife? (EWMA trading days, enter for 20) ";
const VOL_MAXMULT_Q = "  Vol max mult? (size multiplier cap, enter for 3.0) ";
const VOL_MINDAYS_Q = "  Vol min days? (warm-up days, enter for 30) ";

// /tune variants: every sizing input is a swept list (comma-separated). Empty
// Enter keeps the single default value (one combo for that dimension).
const TUNE_VOL_TARGET_Q = "  Vol target? (e.g. 0.15,0.20 — enter for 0.20) ";
const TUNE_VOL_HALFLIFE_Q = "  Vol halflife? (e.g. 10,20 — enter for 20) ";
const TUNE_VOL_MAXMULT_Q = "  Vol max mult? (e.g. 2,3 — enter for 3.0) ";
const TUNE_VOL_MINDAYS_Q = "  Vol min days? (e.g. 20,30 — enter for 30) ";

// The PRICE SCALE follows the SYMBOL, not the instrument. NQ quotes in the
// thousands and moves in index points; forex pairs quote ~1.x and move in pips.
// NQ can be modeled as a $1/pt CFD (the `forex` instrument), but its price is
// still ~20000 — so the visualizer keys off the symbol via nqSelected(), not
// usesContracts() (which only distinguishes contracts-vs-lots / point value).
fn indexScale() bool {
    return nqSelected();
}

// Representative price used only to visualize how a fill is shifted.
fn refPrice() f64 {
    return if (indexScale()) 20000.0 else 1.10000;
}

// Default cost values are symbol-scale-aware: index points for NQ (1 pt scale),
// pips for forex (0.0001 scale). A forex "point" in this engine is a raw price
// unit, so realistic costs are tiny decimals — using 0.2/0.4 there would imply
// thousands of pips of slippage.
fn slippageDef() f64 {
    return if (indexScale()) 0.2 else 0.0001; // NQ: 0.2 pt | forex: 1 pip
}

fn spreadDef() f64 {
    return if (indexScale()) 4.0 else 0.0002; // NQ: 4 pt | forex: 2 pip
}

// Builds a single-line transaction-cost prompt. The input is a buy fill `A->B`;
// the default is shown the same way (the representative buy fill at the default
// value), so Enter accepts it. `label` is "Spread"/"Slippage", `per_fill_pts` the
// points charged on each fill (slippage = full value; spread = half, since a
// market order crosses only one side).
fn costQuestion(buf: []u8, label: []const u8, per_fill_pts: f64) []const u8 {
    const ref = refPrice();
    const buy = ref + per_fill_pts;
    const fallback = "  Slippage? (buy fill A->B) ";
    if (indexScale()) {
        return std.fmt.bufPrint(buf, "  {s}? (buy fill A->B, enter for {d:.2}->{d:.2}) ", .{ label, ref, buy }) catch fallback;
    }
    return std.fmt.bufPrint(buf, "  {s}? (buy fill A->B, enter for {d:.5}->{d:.5}) ", .{ label, ref, buy }) catch fallback;
}

fn spreadQuestion(buf: []u8) []const u8 {
    return costQuestion(buf, "Spread", spreadDef() / 2.0);
}

fn slippageQuestion(buf: []u8) []const u8 {
    return costQuestion(buf, "Slippage", slippageDef());
}

// ── Bar rendering ─────────────────────────────────────────────────────────────

fn buildSep(buf: []u8) []const u8 {
    const dash = "─";
    const count = @min(term_cols, buf.len / dash.len);
    var pos: usize = 0;
    for (0..count) |_| {
        @memcpy(buf[pos..][0..dash.len], dash);
        pos += dash.len;
    }
    return buf[0..pos];
}

fn drawBar(input: []const u8) void {
    var wbuf: [8192]u8 = undefined;
    var pos: usize = 0;
    var sep_buf: [3072]u8 = undefined;
    const sep = buildSep(&sep_buf);

    @memcpy(wbuf[pos..][0.."\x1b[?25l".len], "\x1b[?25l");
    pos += "\x1b[?25l".len;

    // Suggestion rows: hidden while running.
    {
        const show = !g_running and input.len > 0 and input[0] == '/';
        var first_match = true;
        for (COMMANDS, 0..) |cmd, i| {
            const row = term_rows - COMMANDS.len - 4 + i;
            const clear = std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};1H\x1b[2K", .{row}) catch return;
            pos += clear.len;
            if (show and std.mem.startsWith(u8, cmd.name, input)) {
                const e = if (first_match and input.len > 1)
                    std.fmt.bufPrint(wbuf[pos..], "  \x1b[1m{s}\x1b[0m  \x1b[90m{s}\x1b[0m", .{ cmd.name, cmd.description })
                else
                    std.fmt.bufPrint(wbuf[pos..], "  \x1b[2m{s}\x1b[0m  \x1b[90m{s}\x1b[0m", .{ cmd.name, cmd.description });
                pos += (e catch return).len;
                first_match = false;
            }
        }
    }

    // Top separator (gray).
    {
        const s = std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};1H\x1b[2K\x1b[90m{s}\x1b[0m", .{ term_rows - 4, sep }) catch return;
        pos += s.len;
    }

    // Input row.
    if (g_running) {
        const s = std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};1H\x1b[2K  \x1b[90mrunning...  Ctrl+C to cancel\x1b[0m", .{term_rows - 3}) catch return;
        pos += s.len;
    } else {
        const s = if (isExactCommand(input))
            std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};1H\x1b[2K > \x1b[36m{s}\x1b[0m", .{ term_rows - 3, input })
        else
            std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};1H\x1b[2K > {s}", .{ term_rows - 3, input });
        pos += (s catch return).len;
    }

    // Bottom separator (gray).
    {
        const s = std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};1H\x1b[2K\x1b[90m{s}\x1b[0m", .{ term_rows - 2, sep }) catch return;
        pos += s.len;
    }

    if (!g_running) {
        const s = std.fmt.bufPrint(wbuf[pos..], "\x1b[{d};{d}H\x1b[?25h", .{ term_rows - 3, 4 + input.len }) catch return;
        pos += s.len;
    }

    out(wbuf[0..pos]);
}

// ── Save prompt ───────────────────────────────────────────────────────────────

fn promptSave(result: engine.Result, summary: db.Summary, strat_name: []const u8, symbol: []const u8, params: db.Params) void {
    printContent("  Save result? (y/n)  ");

    var ch: [1]u8 = undefined;
    const n = posix.read(STDIN, &ch) catch return;
    if (n == 0) return;

    if (ch[0] == 'y' or ch[0] == 'Y') {
        db.save(strat_name, symbol, result, summary, params) catch |err| {
            var buf: [128]u8 = undefined;
            const msg = std.fmt.bufPrint(&buf, "\n  Save failed: {s}\n\n", .{@errorName(err)}) catch "\n  Save failed.\n\n";
            printContent(msg);
            return;
        };
        printContent("\n  Saved.\n\n");
    } else {
        printContent("\n\n");
    }
}

// ── Monte Carlo ─────────────────────────────────────────────────────────────
// Resample a saved backtest's realized trade PnLs (stationary block bootstrap)
// to recover the distribution of final balance / drawdown the edge could have
// produced, then offer to persist the result. MC is fast (~10k sims over a few
// thousand trades is sub-millisecond), so it runs synchronously here — no worker
// thread / Ctrl+C polling needed.
fn runMonteCarlo(io: std.Io, gpa: std.mem.Allocator, entry: db.BacktestEntry, sel_id: i64) void {
    const sname = entry.strategy[0..entry.strategy_len];
    const sym = entry.symbol[0..entry.symbol_len];

    drawBar(""); // clear the input bar before any blocking operation

    // Echo the selection on the question line.
    var hbuf: [64]u8 = undefined;
    out("\x1b8\r\x1b[2K");
    const echoed = std.fmt.bufPrint(&hbuf, "  Select id: {d}\n", .{sel_id}) catch "  Select id:\n";
    out(echoed);
    out("\x1b7");

    const balance = db.loadInitialBalance(entry.id) catch {
        printContent("\n  Could not load backtest.\n\n");
        return;
    };

    // Load the trade PnL series (capped; strategies rarely exceed this).
    const MAX_TRADES = 200_000;
    const pnls = gpa.alloc(f64, MAX_TRADES) catch {
        printContent("\n  Out of memory.\n\n");
        return;
    };
    defer gpa.free(pnls);
    const ntr = db.loadTradePnls(entry.id, pnls) catch {
        printContent("\n  Could not load trades.\n\n");
        return;
    };
    if (ntr == 0) {
        printContent("\n  That backtest has no trades.\n\n");
        return;
    }

    // Capture every sim's equity curve too, so the saved spaghetti is the same
    // set of simulations behind the summary stats (default 1000 sims).
    var paths: ?montecarlo.Paths = null;
    const mc = montecarlo.run(gpa, pnls[0..ntr], balance, .{}, &paths) catch {
        printContent("\n  Monte Carlo failed.\n\n");
        return;
    };
    defer if (paths) |*p| p.deinit(gpa);

    out("\x1b8"); // restore content cursor so the report flows in the content area
    report.printMonteCarlo(io, sname, sym, mc) catch {
        out("\x1b7");
        return;
    };
    out("\x1b7");

    promptSaveMonteCarlo(entry.id, sname, sym, mc, paths);
}

fn promptSaveMonteCarlo(source_id: i64, sname: []const u8, sym: []const u8, mc: montecarlo.Result, paths: ?montecarlo.Paths) void {
    printContent("  Save Monte Carlo result? (y/n)  ");

    var ch: [1]u8 = undefined;
    const n = posix.read(STDIN, &ch) catch return;
    if (n == 0) return;

    if (ch[0] == 'y' or ch[0] == 'Y') {
        const mc_id = db.saveMonteCarlo(source_id, sname, sym, mc, paths) catch |err| {
            var buf: [128]u8 = undefined;
            const msg = std.fmt.bufPrint(&buf, "\n  Save failed: {s}\n\n", .{@errorName(err)}) catch "\n  Save failed.\n\n";
            printContent(msg);
            return;
        };
        var buf: [256]u8 = undefined;
        const msg = if (paths) |p|
            std.fmt.bufPrint(&buf, "\n  Saved.  mc_id={d}  ({d} chart paths x {d} steps in montecarlo_paths)\n\n", .{ mc_id, p.n_paths, p.n_steps }) catch "\n  Saved.\n\n"
        else
            std.fmt.bufPrint(&buf, "\n  Saved.  mc_id={d}\n\n", .{mc_id}) catch "\n  Saved.\n\n";
        printContent(msg);
    } else {
        printContent("\n\n");
    }
}

// ── Strategy execution ────────────────────────────────────────────────────────
// Generic over the strategy type so every strategy reuses the same worker
// thread + Ctrl+C polling + report + save plumbing.

fn RunCtx(comptime S: type) type {
    return struct {
        io: std.Io,
        gpa: std.mem.Allocator,
        strat: *S,
        result: ?engine.Result = null,
        err: ?anyerror = null,
        done: std.atomic.Value(bool) = .init(false),
    };
}

fn runAndReport(comptime S: type, io: std.Io, gpa: std.mem.Allocator, strat: *S, save_name: []const u8, save_symbol: []const u8, params: db.Params) void {
    g_running = true;
    engine.cancelled.store(false, .monotonic);
    drawBar("");

    const Ctx = RunCtx(S);
    const Worker = struct {
        fn run(ctx: *Ctx) void {
            if (engine.run(ctx.io, ctx.gpa, ctx.strat)) |result| {
                ctx.result = result;
            } else |err| {
                ctx.err = err;
            }
            ctx.done.store(true, .release);
        }
    };

    var ctx = Ctx{ .io = io, .gpa = gpa, .strat = strat };
    const t = std.Thread.spawn(.{}, Worker.run, .{&ctx}) catch |err| {
        g_running = false;
        var errbuf: [128]u8 = undefined;
        const msg = std.fmt.bufPrint(&errbuf, "  Error: {s}\n\n", .{@errorName(err)}) catch "  Error.\n\n";
        printContent(msg);
        return;
    };

    var pfd = [1]posix.pollfd{.{ .fd = STDIN, .events = posix.POLL.IN, .revents = 0 }};
    while (!ctx.done.load(.acquire)) {
        const ready = posix.poll(&pfd, 10) catch 0;
        if (ready > 0 and pfd[0].revents & posix.POLL.IN != 0) {
            var ch: [1]u8 = undefined;
            const n = posix.read(STDIN, &ch) catch break;
            if (n > 0 and ch[0] == 3) engine.cancelled.store(true, .release);
        }
    }
    t.join();

    g_running = false;
    engine.cancelled.store(false, .monotonic);

    if (ctx.err) |err| {
        if (err == error.Cancelled) {
            printContent("  Cancelled.\n\n");
        } else {
            var errbuf: [128]u8 = undefined;
            const msg = std.fmt.bufPrint(&errbuf, "  Error: {s}\n\n", .{@errorName(err)}) catch "  Error.\n\n";
            printContent(msg);
        }
    } else {
        const result = ctx.result.?;
        defer result.deinit(gpa);
        out("\x1b8"); // restore content cursor so the report flows in the content area
        const summary = report.print(io, result) catch {
            out("\x1b7");
            return;
        };
        out("\x1b7"); // save content cursor after the report
        promptSave(result, summary, save_name, save_symbol, params);
    }
}

// Snapshot the active run parameters (sizing/vol/date/cost) into a db.Params so
// the run can be persisted and later re-run by /combine. `base_size` is the base
// lots/contracts before leverage; `contracts = base_size × leverage` at run time.
fn currentParams(base_size: f64, leverage: f64) db.Params {
    return .{
        .base_size = base_size,
        .leverage = leverage,
        .sizing_mode = if (g_sizing_mode == .vol_target) 1 else 0,
        .vol_target = g_vol.target,
        .vol_halflife = g_vol.halflife,
        .vol_max_mult = g_vol.max_mult,
        .vol_min_days = @intCast(g_vol.min_days),
        .date_from = engine.from orelse "",
        .date_to = engine.to orelse "",
        .spread = engine.spread,
        .slippage = engine.slippage,
    };
}

// Generic over the strategy type. OrbBuy and RthVwap share the same parameter
// surface (initial_balance, contracts, leverage, sizing_mode, vol) and the same
// /run question flow, so one helper drives both.
fn runStrategy(comptime S: type, io: std.Io, gpa: std.mem.Allocator, balance: f64, base_contracts: f64, leverage: f64, save_name: []const u8) void {
    var strat = S{
        .initial_balance = balance,
        .contracts = base_contracts * leverage,
        .leverage = leverage,
        .sizing_mode = g_sizing_mode,
        .vol = g_vol,
    };
    runAndReport(S, io, gpa, &strat, save_name, SYMBOL_LABELS[g_symbol_idx], currentParams(base_contracts, leverage));
}

fn runBuyHold(io: std.Io, gpa: std.mem.Allocator, balance: f64, lots: f64) void {
    var strat = strategy.BuyHold{ .initial_balance = balance, .contracts = lots };
    const params = db.Params{
        .base_size = lots,
        .leverage = 1,
        .sizing_mode = 0, // Buy & Hold has no sizing model
        .date_from = engine.from orelse "",
        .date_to = engine.to orelse "",
        .spread = engine.spread,
        .slippage = engine.slippage,
    };
    runAndReport(strategy.BuyHold, io, gpa, &strat, "BUY_HOLD", SYMBOL_LABELS[g_symbol_idx], params);
}

// ── Combine ───────────────────────────────────────────────────────────────────
// Merge the trade logs of several saved backtests into one synthetic Result and
// print a single report over the combined equity curve.

fn combineHasId(id: i64) bool {
    for (g_combine_ids[0..g_combine_count]) |x| {
        if (x == id) return true;
    }
    return false;
}

// Order trades by exit timestamp — report.print buckets daily returns by
// exit_ts and assumes the trades arrive in that order.
fn tradeBeforeByExit(_: void, a: engine.Trade, b: engine.Trade) bool {
    return std.mem.order(u8, &a.exit_ts, &b.exit_ts) == .lt;
}

// Assemble a Result from already-merged, exit-sorted trades plus a precomputed
// drawdown (real mark-to-market when bars were available, realized-curve fallback
// otherwise).
fn buildCombinedResult(initial: f64, trades: []engine.Trade, dd: combine.Drawdown) engine.Result {
    var first_ts: engine.Ts = trades[0].entry_ts;
    var last_ts: engine.Ts = trades[0].exit_ts;
    for (trades) |t| {
        if (std.mem.order(u8, &t.entry_ts, &first_ts) == .lt) first_ts = t.entry_ts;
        if (std.mem.order(u8, &t.exit_ts, &last_ts) == .gt) last_ts = t.exit_ts;
    }
    return .{
        .trades = trades,
        .first_ts = first_ts,
        .last_ts = last_ts,
        .initial_balance = initial,
        .max_drawdown = dd.max_drawdown,
        .avg_drawdown = dd.avg_drawdown,
        .max_drawdown_peak_date = dd.max_drawdown_peak_date,
        .max_drawdown_trough_date = dd.max_drawdown_trough_date,
        .max_intraday_drawdown = dd.max_intraday_drawdown,
        .avg_intraday_drawdown = dd.avg_intraday_drawdown,
        .max_intraday_drawdown_date = dd.max_intraday_drawdown_date,
        .max_drawdown_dollars = dd.max_drawdown_dollars,
        .avg_drawdown_dollars = dd.avg_drawdown_dollars,
        .max_intraday_drawdown_dollars = dd.max_intraday_drawdown_dollars,
        .avg_intraday_drawdown_dollars = dd.avg_intraday_drawdown_dollars,
    };
}

// Map a saved backtest's stored labels to the price table and point value needed
// to mark its trades to market. The timeframe comes from the strategy (its
// compile-time decl); the symbol prefix and point value from the stored symbol /
// instrument. Returns table_len = 0 when the source can't be resolved (e.g. a
// previously-saved COMBINED run), so its trades book realized PnL only.
fn combineTimeframe(name: []const u8) ?[]const u8 {
    if (std.mem.eql(u8, name, "30M_BUY")) return strategy.OrbBuy.timeframe;
    if (std.mem.eql(u8, name, "RTH_VWAP")) return strategy.RthVwap.timeframe;
    if (std.mem.eql(u8, name, "BUY_HOLD")) return strategy.BuyHold.timeframe;
    return null;
}

fn combineSymbolPrefix(label: []const u8) ?[]const u8 {
    for (SYMBOL_LABELS, 0..) |l, i| {
        if (std.mem.eql(u8, l, label)) return SYMBOL_PREFIXES[i];
    }
    return null;
}

fn combineMult(instrument: []const u8) f64 {
    if (std.mem.eql(u8, instrument, "nq mini")) return 20.0;
    if (std.mem.eql(u8, instrument, "nq micro")) return 2.0;
    return 1.0;
}

fn combineInstrument(label: []const u8) ?engine.Instrument {
    if (std.mem.eql(u8, label, "forex")) return .forex;
    if (std.mem.eql(u8, label, "nq mini")) return .nq_mini;
    if (std.mem.eql(u8, label, "nq micro")) return .nq_micro;
    return null;
}

fn combineFindEntry(id: i64) ?db.BacktestEntry {
    for (g_delete_entries[0..g_delete_count]) |entry| {
        if (entry.id == id) return entry;
    }
    return null;
}

// Map a stored sizing_mode int (0 none / 1 vol target) back to the enum.
fn combineMode(v: i64) strategy.sizing.Mode {
    return if (v == 1) .vol_target else .none;
}

// Rebuild a VolTarget from a saved entry's params (only consulted when the saved
// sizing_mode is vol target).
fn combineVol(e: *const db.BacktestEntry) strategy.sizing.VolTarget {
    return .{
        .target = e.vol_target,
        .halflife = e.vol_halflife,
        .max_mult = e.vol_max_mult,
        .min_days = @intCast(@max(@as(i64, 0), e.vol_min_days)),
    };
}

// A saved row is usable in /combine only if it carries run parameters (rows saved
// before parameter persistence have base_size 0) and is not itself a combined run.
fn combineEntryUsable(e: db.BacktestEntry) bool {
    if (std.mem.eql(u8, e.strategy[0..e.strategy_len], "COMBINED")) return false;
    return e.base_size > 0;
}

// Re-run one saved config through the engine and return its fresh trade log.
// Builds an explicit engine.Config from the saved params (no global mutation),
// so several sources can run concurrently without racing engine state. Position
// sizing is balance-independent, so running at the combine-level balance
// reproduces the saved run's trades.
fn runOneSource(io: std.Io, gpa: std.mem.Allocator, e: *const db.BacktestEntry, balance: f64) !engine.Result {
    const cfg = engine.Config{
        .symbol = combineSymbolPrefix(e.symbol[0..e.symbol_len]) orelse return error.UnknownSymbol,
        .instrument = combineInstrument(e.instrument[0..e.instrument_len]) orelse .forex,
        .from = if (e.date_from_len > 0) e.date_from[0..e.date_from_len] else null,
        .to = if (e.date_to_len > 0) e.date_to[0..e.date_to_len] else null,
        .spread = e.spread,
        .slippage = e.slippage,
        .warmup_days = engine.warmup_days,
    };

    const sname = e.strategy[0..e.strategy_len];
    if (std.mem.eql(u8, sname, "30M_BUY")) {
        var s = strategy.OrbBuy{
            .initial_balance = balance,
            .contracts = e.base_size * e.leverage,
            .leverage = e.leverage,
            .sizing_mode = combineMode(e.sizing_mode),
            .vol = combineVol(e),
        };
        return engine.runWith(io, gpa, &s, cfg);
    } else if (std.mem.eql(u8, sname, "RTH_VWAP")) {
        var s = strategy.RthVwap{
            .initial_balance = balance,
            .contracts = e.base_size * e.leverage,
            .leverage = e.leverage,
            .sizing_mode = combineMode(e.sizing_mode),
            .vol = combineVol(e),
        };
        return engine.runWith(io, gpa, &s, cfg);
    } else if (std.mem.eql(u8, sname, "BUY_HOLD")) {
        var s = strategy.BuyHold{ .initial_balance = balance, .contracts = e.base_size };
        return engine.runWith(io, gpa, &s, cfg);
    }
    return error.UnknownStrategy;
}

// Result of the combine worker: one synthetic Result over the merged book, the
// shared instrument (null when sources mix instruments), and whether the drawdown
// fell back to the trade-close estimate (no bars fetchable).
const CombineOut = struct { result: engine.Result, common_inst: ?engine.Instrument, fallback: bool };

// One source's re-run: its own thread does the fetch + backtest, writes its fresh
// trade log (or error) here, and bumps the shared progress counter when done.
const SourceCtx = struct {
    io: std.Io,
    gpa: std.mem.Allocator,
    entry: *const db.BacktestEntry,
    balance: f64,
    progress: *std.atomic.Value(usize),
    trades: ?[]engine.Trade = null,
    err: ?anyerror = null,
};

fn sourceThread(sc: *SourceCtx) void {
    if (runOneSource(sc.io, sc.gpa, sc.entry, sc.balance)) |res| {
        sc.trades = res.trades;
    } else |err| {
        sc.err = err;
    }
    _ = sc.progress.fetchAdd(1, .release);
}

// Re-run every picked config CONCURRENTLY (one thread per source — each opens its
// own QuestDB connection and runs an independent engine pass), then merge their
// trade logs and mark the combined book to market for a real portfolio drawdown.
// Caller owns the returned result.trades.
fn combineCompute(io: std.Io, gpa: std.mem.Allocator, balance: f64, entries: []const db.BacktestEntry, progress: *std.atomic.Value(usize)) !CombineOut {
    const ne = entries.len;

    // Launch all sources at once; if a spawn fails, run that source inline.
    var scs: [MAX_COMBINE]SourceCtx = undefined;
    var threads: [MAX_COMBINE]?std.Thread = undefined;
    for (0..ne) |i| {
        scs[i] = .{ .io = io, .gpa = gpa, .entry = &entries[i], .balance = balance, .progress = progress };
    }
    for (0..ne) |i| {
        threads[i] = std.Thread.spawn(.{}, sourceThread, .{&scs[i]}) catch blk: {
            sourceThread(&scs[i]);
            break :blk null;
        };
    }
    for (0..ne) |i| if (threads[i]) |t| t.join();

    // If any source failed, free the ones that succeeded and surface the error
    // (Cancelled wins so Ctrl+C reads as a cancel, not a generic error).
    var first_err: ?anyerror = null;
    for (0..ne) |i| {
        if (scs[i].err) |e| {
            if (first_err == null or e == error.Cancelled) first_err = e;
        }
    }
    if (first_err) |e| {
        for (0..ne) |i| if (scs[i].trades) |t| gpa.free(t);
        return e;
    }

    // Collect each source's fresh trades + its price table / point value.
    var loaded: [MAX_COMBINE][]engine.Trade = undefined;
    var srcs: [MAX_COMBINE]combine.TradeSrc = undefined;
    var common_inst: ?engine.Instrument = null;
    var inst_seen = false;
    for (0..ne) |i| {
        const entry = &entries[i];
        loaded[i] = scs[i].trades.?;

        const inst = combineInstrument(entry.instrument[0..entry.instrument_len]);
        if (!inst_seen) {
            common_inst = inst;
            inst_seen = true;
        } else if (common_inst != inst) {
            common_inst = null; // mixed instruments
        }

        var src = combine.TradeSrc{ .trades = loaded[i], .mult = combineMult(entry.instrument[0..entry.instrument_len]) };
        if (combineSymbolPrefix(entry.symbol[0..entry.symbol_len])) |prefix| {
            if (combineTimeframe(entry.strategy[0..entry.strategy_len])) |tf| {
                const tbl = std.fmt.bufPrint(&src.table_buf, "{s}_{s}", .{ prefix, tf }) catch "";
                src.table_len = tbl.len;
            }
        }
        srcs[i] = src;
    }
    errdefer for (loaded[0..ne]) |s| gpa.free(s);

    // Merge every source's trades into one exit-sorted log for the report.
    var all: std.ArrayList(engine.Trade) = .empty;
    errdefer all.deinit(gpa);
    for (srcs[0..ne]) |s| try all.appendSlice(gpa, s.trades);
    if (all.items.len == 0) return error.NoTrades;
    const owned = try all.toOwnedSlice(gpa);
    errdefer gpa.free(owned);
    std.mem.sort(engine.Trade, owned, {}, tradeBeforeByExit);

    // Real drawdown: re-fetch each source's bars and mark the combined book to
    // market. Falls back to a realized-equity drawdown if no bars can be fetched.
    var fallback = false;
    const dd = combine.markToMarket(io, gpa, balance, srcs[0..ne]) catch blk: {
        fallback = true;
        break :blk combine.realizedDrawdown(balance, owned);
    };
    const result = buildCombinedResult(balance, owned, dd);

    // The per-source trade slices were copied into `owned`; free them now.
    for (loaded[0..ne]) |s| gpa.free(s);
    return .{ .result = result, .common_inst = common_inst, .fallback = fallback };
}

const CombineCtx = struct {
    io: std.Io,
    gpa: std.mem.Allocator,
    balance: f64,
    entries: []const db.BacktestEntry,
    progress: std.atomic.Value(usize) = .init(0),
    out: ?CombineOut = null,
    err: ?anyerror = null,
    done: std.atomic.Value(bool) = .init(false),
};

fn combineThread(ctx: *CombineCtx) void {
    if (combineCompute(ctx.io, ctx.gpa, ctx.balance, ctx.entries, &ctx.progress)) |o| {
        ctx.out = o;
    } else |err| {
        ctx.err = err;
    }
    ctx.done.store(true, .release);
}

fn drawCombineProgress(done: usize, total: usize) void {
    var buf: [128]u8 = undefined;
    const s = std.fmt.bufPrint(&buf, "\x1b[{d};1H\x1b[2K  \x1b[90mrunning {d} strategies together ({d} done)  Ctrl+C to cancel\x1b[0m", .{ term_rows - 3, total, done }) catch return;
    out(s);
}

// Re-run the picked saved configs and print one portfolio report over the merged
// book. The heavy work (fetch + backtest per source + mark-to-market) runs on a
// worker thread; the main thread polls stdin so Ctrl+C cancels.
fn runCombine(io: std.Io, gpa: std.mem.Allocator, balance: f64, ids: []const i64) void {
    // Snapshot the picked entries (with their saved params) into stable storage
    // the worker can read without racing a re-list of g_delete_entries.
    var ne: usize = 0;
    for (ids) |id| {
        const entry = combineFindEntry(id) orelse continue;
        if (!combineEntryUsable(entry)) continue;
        g_combine_entries[ne] = entry;
        ne += 1;
    }
    if (ne == 0) {
        printContent("  Nothing to combine.\n\n");
        return;
    }

    g_running = true;
    engine.cancelled.store(false, .monotonic);
    drawBar("");
    drawCombineProgress(0, ne);

    var ctx = CombineCtx{ .io = io, .gpa = gpa, .balance = balance, .entries = g_combine_entries[0..ne] };
    const t = std.Thread.spawn(.{}, combineThread, .{&ctx}) catch |err| {
        g_running = false;
        var errbuf: [128]u8 = undefined;
        const msg = std.fmt.bufPrint(&errbuf, "  Error: {s}\n\n", .{@errorName(err)}) catch "  Error.\n\n";
        printContent(msg);
        return;
    };

    var last_done: usize = 0;
    var pfd = [1]posix.pollfd{.{ .fd = STDIN, .events = posix.POLL.IN, .revents = 0 }};
    while (!ctx.done.load(.acquire)) {
        const ready = posix.poll(&pfd, 10) catch 0;
        if (ready > 0 and pfd[0].revents & posix.POLL.IN != 0) {
            var ch: [1]u8 = undefined;
            const n = posix.read(STDIN, &ch) catch break;
            if (n > 0 and ch[0] == 3) engine.cancelled.store(true, .release);
        }
        const done = ctx.progress.load(.acquire);
        if (done != last_done) {
            drawCombineProgress(done, ne);
            last_done = done;
        }
    }
    t.join();

    g_running = false;
    engine.cancelled.store(false, .monotonic);

    if (ctx.err) |err| {
        if (err == error.Cancelled) {
            printContent("  Cancelled.\n\n");
        } else if (err == error.NoTrades) {
            printContent("  No trades to combine.\n\n");
        } else {
            var errbuf: [128]u8 = undefined;
            const msg = std.fmt.bufPrint(&errbuf, "  Error: {s}\n\n", .{@errorName(err)}) catch "  Error.\n\n";
            printContent(msg);
        }
        return;
    }

    const o = ctx.out.?;
    const result = o.result;
    defer result.deinit(gpa);

    if (o.fallback) printContent("  (couldn't fetch bars — drawdown is a trade-close estimate)\n");

    // Show the sources' instrument (label + lot/contract terminology) when they
    // share one; fall back to forex (generic "lots") for a mixed combine.
    engine.instrument = o.common_inst orelse .forex;

    out("\x1b8"); // restore content cursor so the report flows in the content area
    const summary = report.print(io, result) catch {
        out("\x1b7");
        return;
    };
    out("\x1b7"); // save content cursor after the report
    // Combined runs mix strategies/symbols, so they're saved under a generic
    // "COMBINED" / "mixed" label with empty params (not re-runnable as one strategy).
    promptSave(result, summary, "COMBINED", "mixed", .{});
}

// Render the "pick a backtest" prompt: the ids picked so far, the still-available
// backtests, and the input prompt. Parks the content cursor at the prompt.
fn renderCombinePick() void {
    var buf: [4096]u8 = undefined;
    var pos: usize = 0;
    if (g_combine_count > 0) {
        const p = std.fmt.bufPrint(buf[pos..], "  Picked: ", .{}) catch return;
        pos += p.len;
        for (g_combine_ids[0..g_combine_count], 0..) |id, i| {
            const s = std.fmt.bufPrint(buf[pos..], "{s}#{d}", .{ if (i == 0) "" else ", ", id }) catch break;
            pos += s.len;
        }
        if (pos < buf.len) {
            buf[pos] = '\n';
            pos += 1;
        }
    }
    const ah = std.fmt.bufPrint(buf[pos..], "  Available:\n", .{}) catch return;
    pos += ah.len;
    for (g_delete_entries[0..g_delete_count]) |entry| {
        if (combineHasId(entry.id)) continue;
        if (!combineEntryUsable(entry)) continue;
        const sname = entry.strategy[0..entry.strategy_len];
        const sym = entry.symbol[0..entry.symbol_len];
        const inst = entry.instrument[0..entry.instrument_len];
        // Param hint so configs of the same strategy are distinguishable: base
        // size, sizing mode, and the date range the trades will be re-run over.
        const sizing_label: []const u8 = if (entry.sizing_mode == 1) "voltgt" else "fixed";
        const dfrom = if (entry.date_from_len >= 4) entry.date_from[0..4] else "????";
        const dto = if (entry.date_to_len >= 4) entry.date_to[0..4] else "????";
        const s = std.fmt.bufPrint(buf[pos..], "    #{d}  {s}  {s}  {s}  size {d:.2}  {s}  {s}-{s}\n", .{ entry.id, sname, sym, inst, entry.base_size, sizing_label, dfrom, dto }) catch break;
        pos += s.len;
    }
    // Once at least one is picked, an empty Enter runs the combination.
    const q = if (g_combine_count > 0)
        std.fmt.bufPrint(buf[pos..], "\n  Select id (enter to run): ", .{}) catch ""
    else
        std.fmt.bufPrint(buf[pos..], "\n  Select id: ", .{}) catch "";
    pos += q.len;
    printContent(buf[0..pos]);
}

// ── Tuning ────────────────────────────────────────────────────────────────────

const TuneCtx = struct {
    io: std.Io,
    gpa: std.mem.Allocator,
    grid: tune.OrbGrid,
    progress: std.atomic.Value(usize) = .init(0),
    combos: ?[]tune.OrbCombo = null,
    err: ?anyerror = null,
    done: std.atomic.Value(bool) = .init(false),
};

// Generic over the strategy type so the same tuner plumbing drives every
// tunable strategy (OrbBuy, RthVwap, …). Returns the concrete thread entry.
fn tuneThread(comptime S: type) fn (*TuneCtx) void {
    return struct {
        fn run(ctx: *TuneCtx) void {
            if (tune.runOrb(S, ctx.io, ctx.gpa, ctx.grid, &ctx.progress)) |combos| {
                ctx.combos = combos;
            } else |err| {
                ctx.err = err;
            }
            ctx.done.store(true, .release);
        }
    }.run;
}

fn drawTuneProgress(done: usize, total: usize) void {
    var buf: [128]u8 = undefined;
    const s = std.fmt.bufPrint(&buf, "\x1b[{d};1H\x1b[2K  \x1b[90mran {d}/{d} combinations  Ctrl+C to cancel\x1b[0m", .{ term_rows - 3, done, total }) catch return;
    out(s);
}

fn runTune(comptime S: type, io: std.Io, gpa: std.mem.Allocator, grid: tune.OrbGrid) void {
    g_running = true;
    engine.cancelled.store(false, .monotonic);
    drawBar("");

    const total = tune.totalOrb(grid);
    drawTuneProgress(0, total);

    var ctx = TuneCtx{ .io = io, .gpa = gpa, .grid = grid };
    const t = std.Thread.spawn(.{}, tuneThread(S), .{&ctx}) catch |err| {
        g_running = false;
        var errbuf: [128]u8 = undefined;
        const msg = std.fmt.bufPrint(&errbuf, "  Error: {s}\n\n", .{@errorName(err)}) catch "  Error.\n\n";
        printContent(msg);
        return;
    };

    var last_done: usize = 0;
    var pfd = [1]posix.pollfd{.{ .fd = STDIN, .events = posix.POLL.IN, .revents = 0 }};
    while (!ctx.done.load(.acquire)) {
        const ready = posix.poll(&pfd, 10) catch 0;
        if (ready > 0 and pfd[0].revents & posix.POLL.IN != 0) {
            var ch: [1]u8 = undefined;
            const n = posix.read(STDIN, &ch) catch break;
            if (n > 0 and ch[0] == 3) engine.cancelled.store(true, .release);
        }
        const done = ctx.progress.load(.acquire);
        if (done != last_done) {
            drawTuneProgress(done, total);
            last_done = done;
        }
    }
    t.join();

    g_running = false;
    engine.cancelled.store(false, .monotonic);

    if (ctx.err) |err| {
        if (err == error.Cancelled) {
            printContent("  Cancelled.\n\n");
        } else {
            var errbuf: [128]u8 = undefined;
            const msg = std.fmt.bufPrint(&errbuf, "  Error: {s}\n\n", .{@errorName(err)}) catch "  Error.\n\n";
            printContent(msg);
        }
    } else {
        const combos = ctx.combos.?;
        defer gpa.free(combos);
        out("\x1b8"); // restore content cursor so the report flows in the content area
        tune.printReportOrb(io, gpa, combos, ctx.grid.sizing_mode) catch {};
        out("\x1b7"); // save content cursor after the report
    }
}

// ── Parse helpers ─────────────────────────────────────────────────────────────

// Vol-param parsers: empty input keeps the existing (default) value; otherwise
// parse into `dst`. Return false on a malformed number so the caller can fail.
fn parseVolFloat(cmd: []const u8, dst: *f64) bool {
    if (cmd.len == 0) return true;
    dst.* = std.fmt.parseFloat(f64, cmd) catch return false;
    return true;
}

fn parseVolUint(cmd: []const u8, dst: *u32) bool {
    if (cmd.len == 0) return true;
    const v = std.fmt.parseFloat(f64, cmd) catch return false; // accept "30" or "30.0"
    if (v < 0) return false;
    dst.* = @intFromFloat(v);
    return true;
}

// Splits an "A->B" / "A→B" price-move into its two numbers. Returns null if no
// arrow is present or either side fails to parse.
fn parseArrow(s: []const u8) ?struct { a: f64, b: f64 } {
    const i = std.mem.indexOf(u8, s, "->") orelse
        std.mem.indexOf(u8, s, "\xe2\x86\x92") orelse return null;
    const arrow_len: usize = if (s[i] == '-') 2 else 3; // "->" vs UTF-8 "→"
    const a = std.fmt.parseFloat(f64, std.mem.trim(u8, s[0..i], " ")) catch return null;
    const b = std.fmt.parseFloat(f64, std.mem.trim(u8, s[i + arrow_len ..], " ")) catch return null;
    return .{ .a = a, .b = b };
}

// Parses a transaction-cost input into `dst` (engine.spread / engine.slippage).
// Two forms:
//   plain number  — the value in points (e.g. "0.2", "0").
//   "A->B" arrow  — a per-fill price move; per_fill = |B-A|. Slippage charges the
//                   full value per fill, so dst = per_fill; spread charges half
//                   (a market order crosses one side), so dst = 2×per_fill.
// Empty keeps `def`. On the arrow form, `out_ref` receives A (the buy price) so
// the caller can echo the matching sell fill. Returns false on bad/negative input.
fn applyCost(cmd: []const u8, dst: *f64, def: f64, is_spread: bool, out_ref: *?f64) bool {
    out_ref.* = null;
    dst.* = def;
    const t = std.mem.trim(u8, cmd, " ");
    if (t.len == 0) return true;
    if (parseArrow(t)) |ab| {
        const per_fill = @abs(ab.b - ab.a);
        dst.* = if (is_spread) per_fill * 2.0 else per_fill;
        out_ref.* = ab.a;
        return true;
    }
    const v = std.fmt.parseFloat(f64, t) catch return false;
    if (v < 0) return false;
    dst.* = v;
    return true;
}

fn applySlippage(cmd: []const u8, out_ref: *?f64) bool {
    return applyCost(cmd, &engine.slippage, slippageDef(), false, out_ref);
}

fn applySpread(cmd: []const u8, out_ref: *?f64) bool {
    return applyCost(cmd, &engine.spread, spreadDef(), true, out_ref);
}

// Builds the answered-line echo for a cost question: the resulting points value
// plus the buy/sell fills at the reference price (the typed A, or the default
// representative price). `per_fill` is the points moved on each fill.
fn costEcho(buf: []u8, label: []const u8, points: f64, per_fill: f64, ref: f64) []const u8 {
    if (indexScale())
        return std.fmt.bufPrint(buf, "  {s}? {d} pt  (buy {d:.2}\xe2\x86\x92{d:.2}, sell {d:.2}\xe2\x86\x92{d:.2})", .{ label, points, ref, ref + per_fill, ref, ref - per_fill }) catch "  ?";
    return std.fmt.bufPrint(buf, "  {s}? {d}  (buy {d:.5}\xe2\x86\x92{d:.5}, sell {d:.5}\xe2\x86\x92{d:.5})", .{ label, points, ref, ref + per_fill, ref, ref - per_fill }) catch "  ?";
}

fn parseFloatList(s: []const u8, dst: []f64) !usize {
    var n: usize = 0;
    var it = std.mem.tokenizeAny(u8, s, ", ");
    while (it.next()) |tok| {
        if (n >= dst.len) break;
        dst[n] = try std.fmt.parseFloat(f64, tok);
        n += 1;
    }
    if (n == 0) return error.Empty;
    return n;
}

// Like parseFloatList but empty input yields a single-element list holding the
// default — so an Enter on a swept sizing question keeps that param fixed.
// Returns null on malformed input.
fn parseFloatListOrDefault(s: []const u8, dst: []f64, def: f64) ?usize {
    const t = std.mem.trim(u8, s, " ");
    if (t.len == 0) {
        dst[0] = def;
        return 1;
    }
    return parseFloatList(t, dst) catch null;
}

// Whole-number variant (vol min days). Accepts "20" or "20.0"; negatives reject.
fn parseUintListOrDefault(s: []const u8, dst: []u32, def: u32) ?usize {
    const t = std.mem.trim(u8, s, " ");
    if (t.len == 0) {
        dst[0] = def;
        return 1;
    }
    var n: usize = 0;
    var it = std.mem.tokenizeAny(u8, t, ", ");
    while (it.next()) |tok| {
        if (n >= dst.len) break;
        const v = std.fmt.parseFloat(f64, tok) catch return null;
        if (v < 0) return null;
        dst[n] = @intFromFloat(v);
        n += 1;
    }
    if (n == 0) return null;
    return n;
}

// Fill all four vol-param sweep lists with a single default value. Used when
// /tune sizing is .none so those dimensions contribute one (inert) combo each.
fn tuneVolSweepDefaults() void {
    const d = strategy.sizing.VolTarget{};
    g_tune_vol_target[0] = d.target;
    g_tune_vol_target_n = 1;
    g_tune_vol_halflife[0] = d.halflife;
    g_tune_vol_halflife_n = 1;
    g_tune_vol_maxmult[0] = d.max_mult;
    g_tune_vol_maxmult_n = 1;
    g_tune_vol_mindays[0] = d.min_days;
    g_tune_vol_mindays_n = 1;
}

// Parses a "YYYY-YYYY" date range (or empty for default 2018-2025) into
// g_from_buf/g_to_buf and sets engine.from/engine.to. Returns false on bad input.
fn applyDateRange(cmd: []const u8) bool {
    const t = std.mem.trim(u8, cmd, " ");
    if (t.len == 0) {
        @memcpy(g_from_buf[0..10], "2018-01-01");
        g_from_len = 10;
        @memcpy(g_to_buf[0..10], "2025-12-31");
        g_to_len = 10;
    } else if (t.len == 9 and t[4] == '-') {
        const fs = std.fmt.bufPrint(&g_from_buf, "{s}-01-01", .{t[0..4]}) catch return false;
        g_from_len = fs.len;
        const ts = std.fmt.bufPrint(&g_to_buf, "{s}-12-31", .{t[5..9]}) catch return false;
        g_to_len = ts.len;
    } else {
        return false;
    }
    engine.from = g_from_buf[0..g_from_len];
    engine.to = g_to_buf[0..g_to_len];
    return true;
}

// ── Main loop ─────────────────────────────────────────────────────────────────

pub fn run(io: std.Io, gpa: std.mem.Allocator) !void {
    refreshTermSize();
    try enableRawMode();
    defer disableRawMode();

    enterFullscreen();
    defer exitFullscreen();

    var input: [256]u8 = undefined;
    var input_len: usize = 0;
    var state: State = .idle;

    drawBar(input[0..0]);

    while (true) {
        var ch: [1]u8 = undefined;
        const n = try posix.read(STDIN, &ch);
        if (n == 0) continue;

        switch (ch[0]) {
            '\r', '\n' => {
                const cmd = std.mem.trimEnd(u8, input[0..input_len], " ");
                input_len = 0;

                switch (state) {
                    .idle => {
                        if (cmd.len == 0) {
                            drawBar(input[0..0]);
                        } else if (std.mem.eql(u8, cmd, "/exit")) {
                            return;
                        } else if (std.mem.eql(u8, cmd, "/run")) {
                            var sqbuf: [256]u8 = undefined;
                            startFlow("/run", strategyQuestion(&sqbuf));
                            state = .awaiting_strategy;
                            drawBar(input[0..0]);
                        } else if (std.mem.eql(u8, cmd, "/tune")) {
                            var sqbuf: [256]u8 = undefined;
                            startFlow("/tune", strategyQuestion(&sqbuf));
                            state = .awaiting_tune_strategy;
                            drawBar(input[0..0]);
                        } else if (std.mem.eql(u8, cmd, "/delete")) {
                            g_delete_count = db.list(&g_delete_entries) catch 0;
                            if (g_delete_count == 0) {
                                printContent("\x1b[100m\x1b[1m  > /delete\x1b[K\x1b[0m\n\n  No saved backtests.\n\n");
                            } else {
                                var dbuf: [4096]u8 = undefined;
                                var dpos: usize = 0;
                                const hdr = std.fmt.bufPrint(dbuf[dpos..], "\x1b[100m\x1b[1m  > /delete\x1b[K\x1b[0m\n\n", .{}) catch "";
                                dpos += hdr.len;
                                for (g_delete_entries[0..g_delete_count]) |entry| {
                                    const sname = entry.strategy[0..entry.strategy_len];
                                    const sym = entry.symbol[0..entry.symbol_len];
                                    const inst = entry.instrument[0..entry.instrument_len];
                                    const s = std.fmt.bufPrint(dbuf[dpos..], "  #{d}  {s}  {s}  {s}\n", .{ entry.id, sname, sym, inst }) catch break;
                                    dpos += s.len;
                                }
                                // Blank line then question (no trailing \n — cursor parked here).
                                const q = std.fmt.bufPrint(dbuf[dpos..], "\n  Select id: ", .{}) catch "";
                                dpos += q.len;
                                out("\x1b8");
                                out(dbuf[0..dpos]);
                                out("\x1b7");
                                state = .awaiting_delete;
                            }
                            drawBar(input[0..0]);
                        } else if (std.mem.eql(u8, cmd, "/combine")) {
                            startFlow("/combine", "  Initial balance? (enter for 1000) ");
                            state = .awaiting_combine_balance;
                            drawBar(input[0..0]);
                        } else if (std.mem.eql(u8, cmd, "/montecarlo")) {
                            g_delete_count = db.list(&g_delete_entries) catch 0;
                            if (g_delete_count == 0) {
                                printContent("\x1b[100m\x1b[1m  > /montecarlo\x1b[K\x1b[0m\n\n  No saved backtests.\n\n");
                            } else {
                                var dbuf: [4096]u8 = undefined;
                                var dpos: usize = 0;
                                const hdr = std.fmt.bufPrint(dbuf[dpos..], "\x1b[100m\x1b[1m  > /montecarlo\x1b[K\x1b[0m\n\n", .{}) catch "";
                                dpos += hdr.len;
                                for (g_delete_entries[0..g_delete_count]) |entry| {
                                    const sname = entry.strategy[0..entry.strategy_len];
                                    const sym = entry.symbol[0..entry.symbol_len];
                                    const inst = entry.instrument[0..entry.instrument_len];
                                    const s = std.fmt.bufPrint(dbuf[dpos..], "  #{d}  {s}  {s}  {s}\n", .{ entry.id, sname, sym, inst }) catch break;
                                    dpos += s.len;
                                }
                                const q = std.fmt.bufPrint(dbuf[dpos..], "\n  Select id: ", .{}) catch "";
                                dpos += q.len;
                                out("\x1b8");
                                out(dbuf[0..dpos]);
                                out("\x1b7");
                                state = .awaiting_mc_pick;
                            }
                            drawBar(input[0..0]);
                        } else {
                            drawBar(input[0..0]);
                        }
                    },

                    // ── /run flow ───────────────────────────────────────────────
                    .awaiting_strategy => {
                        if (cmd.len > 0) {
                            const idx = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (idx < 1 or idx > STRATEGIES.len) {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            } else {
                                g_strategy_id = idx;
                                const len = @min(cmd.len, g_strategy_sel.len);
                                @memcpy(g_strategy_sel[0..len], cmd[0..len]);
                                g_strategy_sel_len = len;
                                var abuf: [128]u8 = undefined;
                                const answered = std.fmt.bufPrint(&abuf, "  Strategy?  {d}. {s}", .{ idx, STRATEGIES[idx - 1] }) catch "  Strategy?";
                                var sqbuf: [128]u8 = undefined;
                                flowNext(answered, symbolQuestion(&sqbuf));
                                state = .awaiting_symbol;
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_symbol => {
                        if (cmd.len > 0) {
                            const idx = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (idx < 1 or idx > SYMBOL_LABELS.len) {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            } else {
                                g_symbol_idx = idx - 1;
                                var abuf: [128]u8 = undefined;
                                const answered = std.fmt.bufPrint(&abuf, "  Symbol?  {d}. {s}", .{ idx, SYMBOL_LABELS[idx - 1] }) catch "  Symbol?";
                                if (nqSelected()) {
                                    // NQ data: ask how to model it before sizing.
                                    flowNext(answered, INSTRUMENT_Q);
                                    state = .awaiting_instrument;
                                } else {
                                    engine.instrument = .forex;
                                    if (g_strategy_id == STRAT_BUYHOLD) {
                                        flowNext(answered, "  Initial balance? (enter for 1000) ");
                                        state = .awaiting_bh_balance;
                                    } else {
                                        flowNext(answered, "  Initial balance: $?");
                                        state = .awaiting_balance;
                                    }
                                }
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_instrument => {
                        if (cmd.len > 0) {
                            const sel = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (sel < 1 or sel > INSTRUMENTS.len) {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            } else {
                                engine.instrument = INSTRUMENTS[sel - 1];
                                var abuf: [128]u8 = undefined;
                                const answered = std.fmt.bufPrint(&abuf, "  Instrument?  {d}. {s}", .{ sel, INSTRUMENT_LABELS[sel - 1] }) catch "  Instrument?";
                                if (g_strategy_id == STRAT_BUYHOLD) {
                                    flowNext(answered, "  Initial balance? (enter for 1000) ");
                                    state = .awaiting_bh_balance;
                                } else {
                                    flowNext(answered, "  Initial balance: $?");
                                    state = .awaiting_balance;
                                }
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_bh_balance => {
                        g_balance = 1000.0;
                        if (parseVolFloat(cmd, &g_balance)) {
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Initial balance? ${d}", .{g_balance}) catch "  Initial balance? ?";
                            flowNext(answered, if (usesContracts()) "  Contracts? (enter for 1) " else "  Base lot? (enter for 0.1) ");
                            state = .awaiting_bh_lots;
                        } else {
                            flowFail("  Invalid balance.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_bh_lots => {
                        g_base_contracts = if (usesContracts()) 1.0 else 0.1;
                        if (parseVolFloat(cmd, &g_base_contracts)) {
                            var abuf: [128]u8 = undefined;
                            const answered = if (usesContracts())
                                std.fmt.bufPrint(&abuf, "  Contracts? {d}", .{g_base_contracts}) catch "  Contracts? ?"
                            else
                                std.fmt.bufPrint(&abuf, "  Base lot? {d}", .{g_base_contracts}) catch "  Base lot? ?";
                            flowNext(answered, "  Date range? (enter for 2018-2025) ");
                            state = .awaiting_bh_from;
                        } else {
                            flowFail("  Invalid number.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_bh_from => {
                        if (!applyDateRange(cmd)) {
                            flowFail("  Invalid format. Use YYYY-YYYY (e.g. 2018-2025).");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [64]u8 = undefined;
                        const answered = std.fmt.bufPrint(&abuf, "  Date range? {s}-{s}", .{ g_from_buf[0..4], g_to_buf[0..4] }) catch "  Date range? ?";
                        var qbuf: [512]u8 = undefined;
                        flowNext(answered, spreadQuestion(&qbuf));
                        state = .awaiting_bh_spread;
                        drawBar(input[0..0]);
                    },
                    .awaiting_bh_spread => {
                        var spread_ref: ?f64 = null;
                        if (!applySpread(cmd, &spread_ref)) {
                            flowFail("  Invalid spread.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [160]u8 = undefined;
                        const answered = costEcho(&abuf, "Spread", engine.spread, engine.spread / 2.0, spread_ref orelse refPrice());
                        var qbuf: [512]u8 = undefined;
                        flowNext(answered, slippageQuestion(&qbuf));
                        state = .awaiting_bh_slippage;
                        drawBar(input[0..0]);
                    },
                    .awaiting_bh_slippage => {
                        var slip_ref: ?f64 = null;
                        if (!applySlippage(cmd, &slip_ref)) {
                            flowFail("  Invalid slippage.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [160]u8 = undefined;
                        const answered = costEcho(&abuf, "Slippage", engine.slippage, engine.slippage, slip_ref orelse refPrice());
                        flowEnd(answered);
                        engine.symbol = SYMBOL_PREFIXES[g_symbol_idx];
                        runBuyHold(io, gpa, g_balance, g_base_contracts);
                        state = .idle;
                        drawBar(input[0..0]);
                    },
                    .awaiting_balance => {
                        if (cmd.len > 0) {
                            const balance = std.fmt.parseFloat(f64, cmd) catch {
                                flowFail("  Invalid balance.");
                                state = .idle;
                                drawBar(input[0..0]);
                                continue;
                            };
                            g_balance = balance;
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Initial balance: ${s}", .{cmd}) catch "  Initial balance: $?";
                            flowNext(answered, if (usesContracts()) "  Contracts? " else "  Lots? ");
                            state = .awaiting_base_contracts;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_base_contracts => {
                        if (cmd.len > 0) {
                            const base_contracts = std.fmt.parseFloat(f64, cmd) catch {
                                flowFail("  Invalid number.");
                                state = .idle;
                                drawBar(input[0..0]);
                                continue;
                            };
                            g_base_contracts = base_contracts;
                            var abuf: [128]u8 = undefined;
                            const answered = if (usesContracts())
                                std.fmt.bufPrint(&abuf, "  Contracts? {s}", .{cmd}) catch "  Contracts? ?"
                            else
                                std.fmt.bufPrint(&abuf, "  Lots? {s}", .{cmd}) catch "  Lots? ?";
                            flowNext(answered, "  Leverage? (enter for 1) ");
                            state = .awaiting_leverage;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_leverage => {
                        const lev_str = if (cmd.len == 0) "1" else cmd;
                        const lev = std.fmt.parseFloat(f64, lev_str) catch {
                            flowFail("  Invalid leverage.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        };
                        g_leverage = lev;
                        var abuf: [128]u8 = undefined;
                        const answered = std.fmt.bufPrint(&abuf, "  Leverage? {s}", .{lev_str}) catch "  Leverage? ?";
                        flowNext(answered, SIZING_Q);
                        state = .awaiting_sizing;
                        drawBar(input[0..0]);
                    },
                    .awaiting_sizing => {
                        if (cmd.len > 0) {
                            const sel = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (sel == 1) {
                                g_sizing_mode = .none;
                                flowNext("  Sizing?  1. none", "  Date range? (enter for 2018-2025) ");
                                state = .awaiting_from;
                            } else if (sel == 2) {
                                g_sizing_mode = .vol_target;
                                g_vol = .{}; // reset to defaults before the param questions
                                flowNext("  Sizing?  2. vol target", VOL_TARGET_Q);
                                state = .awaiting_vol_target;
                            } else {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_vol_target => {
                        if (parseVolFloat(cmd, &g_vol.target)) {
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol target? {d}", .{g_vol.target}) catch "  Vol target? ?";
                            flowNext(answered, VOL_HALFLIFE_Q);
                            state = .awaiting_vol_halflife;
                        } else {
                            flowFail("  Invalid number.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_vol_halflife => {
                        if (parseVolFloat(cmd, &g_vol.halflife)) {
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol halflife? {d}", .{g_vol.halflife}) catch "  Vol halflife? ?";
                            flowNext(answered, VOL_MAXMULT_Q);
                            state = .awaiting_vol_maxmult;
                        } else {
                            flowFail("  Invalid number.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_vol_maxmult => {
                        if (parseVolFloat(cmd, &g_vol.max_mult)) {
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol max mult? {d}", .{g_vol.max_mult}) catch "  Vol max mult? ?";
                            flowNext(answered, VOL_MINDAYS_Q);
                            state = .awaiting_vol_mindays;
                        } else {
                            flowFail("  Invalid number.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_vol_mindays => {
                        if (parseVolUint(cmd, &g_vol.min_days)) {
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol min days? {d}", .{g_vol.min_days}) catch "  Vol min days? ?";
                            flowNext(answered, "  Date range? (enter for 2018-2025) ");
                            state = .awaiting_from;
                        } else {
                            flowFail("  Invalid number.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_from => {
                        if (!applyDateRange(cmd)) {
                            flowFail("  Invalid format. Use YYYY-YYYY (e.g. 2018-2025).");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [64]u8 = undefined;
                        const answered = std.fmt.bufPrint(&abuf, "  Date range? {s}-{s}", .{ g_from_buf[0..4], g_to_buf[0..4] }) catch "  Date range? ?";
                        var qbuf: [512]u8 = undefined;
                        flowNext(answered, spreadQuestion(&qbuf));
                        state = .awaiting_spread;
                        drawBar(input[0..0]);
                    },
                    .awaiting_spread => {
                        var spread_ref: ?f64 = null;
                        if (!applySpread(cmd, &spread_ref)) {
                            flowFail("  Invalid spread.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [160]u8 = undefined;
                        const answered = costEcho(&abuf, "Spread", engine.spread, engine.spread / 2.0, spread_ref orelse refPrice());
                        var qbuf: [512]u8 = undefined;
                        flowNext(answered, slippageQuestion(&qbuf));
                        state = .awaiting_slippage;
                        drawBar(input[0..0]);
                    },
                    .awaiting_slippage => {
                        var slip_ref: ?f64 = null;
                        if (!applySlippage(cmd, &slip_ref)) {
                            flowFail("  Invalid slippage.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [160]u8 = undefined;
                        const answered = costEcho(&abuf, "Slippage", engine.slippage, engine.slippage, slip_ref orelse refPrice());
                        flowEnd(answered);
                        engine.symbol = SYMBOL_PREFIXES[g_symbol_idx];
                        if (g_strategy_id == STRAT_VWAP) {
                            runStrategy(strategy.RthVwap, io, gpa, g_balance, g_base_contracts, g_leverage, "RTH_VWAP");
                        } else {
                            runStrategy(strategy.OrbBuy, io, gpa, g_balance, g_base_contracts, g_leverage, "30M_BUY");
                        }
                        state = .idle;
                        drawBar(input[0..0]);
                    },

                    // ── /tune flow ──────────────────────────────────────────────
                    .awaiting_tune_strategy => {
                        if (cmd.len > 0) {
                            const idx = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (idx < 1 or idx > STRATEGIES.len) {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            } else if (idx == STRAT_BUYHOLD) {
                                flowFail("  Buy & Hold has no tunable parameters. Use /run.");
                                state = .idle;
                            } else {
                                g_strategy_id = idx;
                                var abuf: [128]u8 = undefined;
                                const answered = std.fmt.bufPrint(&abuf, "  Strategy?  {d}. {s}", .{ idx, STRATEGIES[idx - 1] }) catch "  Strategy?";
                                var sqbuf: [128]u8 = undefined;
                                flowNext(answered, symbolQuestion(&sqbuf));
                                state = .awaiting_tune_symbol;
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_symbol => {
                        if (cmd.len > 0) {
                            const idx = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (idx < 1 or idx > SYMBOL_LABELS.len) {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            } else {
                                g_symbol_idx = idx - 1;
                                var abuf: [128]u8 = undefined;
                                const answered = std.fmt.bufPrint(&abuf, "  Symbol?  {d}. {s}", .{ idx, SYMBOL_LABELS[idx - 1] }) catch "  Symbol?";
                                if (nqSelected()) {
                                    flowNext(answered, INSTRUMENT_Q);
                                    state = .awaiting_tune_instrument;
                                } else {
                                    engine.instrument = .forex;
                                    flowNext(answered, "  Initial balance: $?");
                                    state = .awaiting_tune_balance;
                                }
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_instrument => {
                        if (cmd.len > 0) {
                            const sel = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (sel < 1 or sel > INSTRUMENTS.len) {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            } else {
                                engine.instrument = INSTRUMENTS[sel - 1];
                                var abuf: [128]u8 = undefined;
                                const answered = std.fmt.bufPrint(&abuf, "  Instrument?  {d}. {s}", .{ sel, INSTRUMENT_LABELS[sel - 1] }) catch "  Instrument?";
                                flowNext(answered, "  Initial balance: $?");
                                state = .awaiting_tune_balance;
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_balance => {
                        if (cmd.len > 0) {
                            const balance = std.fmt.parseFloat(f64, cmd) catch {
                                flowFail("  Invalid balance.");
                                state = .idle;
                                drawBar(input[0..0]);
                                continue;
                            };
                            g_tune_balance = balance;
                            var abuf: [128]u8 = undefined;
                            const answered = std.fmt.bufPrint(&abuf, "  Initial balance: ${s}", .{cmd}) catch "  Initial balance: $?";
                            flowNext(answered, if (usesContracts()) "  Base contracts? (e.g. 1,2,3) " else "  Base lots? (e.g. 1,2,3) ");
                            state = .awaiting_tune_base_contracts;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_base_contracts => {
                        if (cmd.len > 0) {
                            g_tune_base_contracts_n = parseFloatList(cmd, &g_tune_base_contracts) catch {
                                flowFail("  Invalid list.");
                                state = .idle;
                                drawBar(input[0..0]);
                                continue;
                            };
                            var abuf: [128]u8 = undefined;
                            const answered = if (usesContracts())
                                std.fmt.bufPrint(&abuf, "  Base contracts? {s}", .{cmd}) catch "  Base contracts? ?"
                            else
                                std.fmt.bufPrint(&abuf, "  Base lots? {s}", .{cmd}) catch "  Base lots? ?";
                            flowNext(answered, "  Leverage? (enter for 1, e.g. 1,2,3) ");
                            state = .awaiting_tune_leverage;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_leverage => {
                        const lev_str = if (cmd.len == 0) "1" else cmd;
                        g_tune_leverage_n = parseFloatList(lev_str, &g_tune_leverage) catch {
                            flowFail("  Invalid list.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        };
                        var abuf: [128]u8 = undefined;
                        const answered = std.fmt.bufPrint(&abuf, "  Leverage? {s}", .{lev_str}) catch "  Leverage? ?";
                        flowNext(answered, SIZING_Q);
                        state = .awaiting_tune_sizing;
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_sizing => {
                        if (cmd.len > 0) {
                            const sel = std.fmt.parseInt(usize, cmd, 10) catch 0;
                            if (sel == 1) {
                                g_sizing_mode = .none;
                                tuneVolSweepDefaults(); // inert vol dims (1 combo each)
                                flowNext("  Sizing?  1. none", "  Date range? (enter for 2018-2025) ");
                                state = .awaiting_tune_from;
                            } else if (sel == 2) {
                                g_sizing_mode = .vol_target;
                                flowNext("  Sizing?  2. vol target", TUNE_VOL_TARGET_Q);
                                state = .awaiting_tune_vol_target;
                            } else {
                                flowFail("  Invalid selection.");
                                state = .idle;
                            }
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_vol_target => {
                        if (parseFloatListOrDefault(cmd, &g_tune_vol_target, 0.20)) |list_n| {
                            g_tune_vol_target_n = list_n;
                            var abuf: [128]u8 = undefined;
                            const shown = if (cmd.len == 0) "0.20" else cmd;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol target? {s}", .{shown}) catch "  Vol target? ?";
                            flowNext(answered, TUNE_VOL_HALFLIFE_Q);
                            state = .awaiting_tune_vol_halflife;
                        } else {
                            flowFail("  Invalid list.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_vol_halflife => {
                        if (parseFloatListOrDefault(cmd, &g_tune_vol_halflife, 20.0)) |list_n| {
                            g_tune_vol_halflife_n = list_n;
                            var abuf: [128]u8 = undefined;
                            const shown = if (cmd.len == 0) "20" else cmd;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol halflife? {s}", .{shown}) catch "  Vol halflife? ?";
                            flowNext(answered, TUNE_VOL_MAXMULT_Q);
                            state = .awaiting_tune_vol_maxmult;
                        } else {
                            flowFail("  Invalid list.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_vol_maxmult => {
                        if (parseFloatListOrDefault(cmd, &g_tune_vol_maxmult, 3.0)) |list_n| {
                            g_tune_vol_maxmult_n = list_n;
                            var abuf: [128]u8 = undefined;
                            const shown = if (cmd.len == 0) "3.0" else cmd;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol max mult? {s}", .{shown}) catch "  Vol max mult? ?";
                            flowNext(answered, TUNE_VOL_MINDAYS_Q);
                            state = .awaiting_tune_vol_mindays;
                        } else {
                            flowFail("  Invalid list.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_vol_mindays => {
                        if (parseUintListOrDefault(cmd, &g_tune_vol_mindays, 30)) |list_n| {
                            g_tune_vol_mindays_n = list_n;
                            var abuf: [128]u8 = undefined;
                            const shown = if (cmd.len == 0) "30" else cmd;
                            const answered = std.fmt.bufPrint(&abuf, "  Vol min days? {s}", .{shown}) catch "  Vol min days? ?";
                            flowNext(answered, "  Date range? (enter for 2018-2025) ");
                            state = .awaiting_tune_from;
                        } else {
                            flowFail("  Invalid list.");
                            state = .idle;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_from => {
                        if (!applyDateRange(cmd)) {
                            flowFail("  Invalid format. Use YYYY-YYYY (e.g. 2018-2025).");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [64]u8 = undefined;
                        const answered = std.fmt.bufPrint(&abuf, "  Date range? {s}-{s}", .{ g_from_buf[0..4], g_to_buf[0..4] }) catch "  Date range? ?";
                        var qbuf: [512]u8 = undefined;
                        flowNext(answered, spreadQuestion(&qbuf));
                        state = .awaiting_tune_spread;
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_spread => {
                        var spread_ref: ?f64 = null;
                        if (!applySpread(cmd, &spread_ref)) {
                            flowFail("  Invalid spread.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [160]u8 = undefined;
                        const answered = costEcho(&abuf, "Spread", engine.spread, engine.spread / 2.0, spread_ref orelse refPrice());
                        var qbuf: [512]u8 = undefined;
                        flowNext(answered, slippageQuestion(&qbuf));
                        state = .awaiting_tune_slippage;
                        drawBar(input[0..0]);
                    },
                    .awaiting_tune_slippage => {
                        var slip_ref: ?f64 = null;
                        if (!applySlippage(cmd, &slip_ref)) {
                            flowFail("  Invalid slippage.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        var abuf: [160]u8 = undefined;
                        const answered = costEcho(&abuf, "Slippage", engine.slippage, engine.slippage, slip_ref orelse refPrice());
                        flowEnd(answered);
                        engine.symbol = SYMBOL_PREFIXES[g_symbol_idx];
                        const grid = tune.OrbGrid{
                            .initial_balance = g_tune_balance,
                            .base_contracts = g_tune_base_contracts[0..g_tune_base_contracts_n],
                            .leverage = g_tune_leverage[0..g_tune_leverage_n],
                            .sizing_mode = g_sizing_mode,
                            .vol_target = g_tune_vol_target[0..g_tune_vol_target_n],
                            .vol_halflife = g_tune_vol_halflife[0..g_tune_vol_halflife_n],
                            .vol_max_mult = g_tune_vol_maxmult[0..g_tune_vol_maxmult_n],
                            .vol_min_days = g_tune_vol_mindays[0..g_tune_vol_mindays_n],
                        };
                        if (g_strategy_id == STRAT_VWAP) {
                            runTune(strategy.RthVwap, io, gpa, grid);
                        } else {
                            runTune(strategy.OrbBuy, io, gpa, grid);
                        }
                        state = .idle;
                        drawBar(input[0..0]);
                    },

                    // ── /delete flow ────────────────────────────────────────────
                    .awaiting_delete => {
                        if (cmd.len > 0) {
                            const sel_id = std.fmt.parseInt(i64, cmd, 10) catch -1;
                            var found: ?db.BacktestEntry = null;
                            for (g_delete_entries[0..g_delete_count]) |entry| {
                                if (entry.id == sel_id) {
                                    found = entry;
                                    break;
                                }
                            }
                            if (found) |entry| {
                                db.delete(entry.id) catch |err| {
                                    var errbuf: [128]u8 = undefined;
                                    const msg = std.fmt.bufPrint(&errbuf, "  Delete failed: {s}", .{@errorName(err)}) catch "  Delete failed.";
                                    flowFail(msg);
                                    state = .idle;
                                    drawBar(input[0..0]);
                                    continue;
                                };
                                const sname = entry.strategy[0..entry.strategy_len];
                                var rbuf: [256]u8 = undefined;
                                out("\x1b8\r\x1b[2K");
                                const result_s = std.fmt.bufPrint(&rbuf, "  Select id: {d}\n\n  Deleted #{d} {s}.\n\n", .{ sel_id, entry.id, sname }) catch "";
                                out(result_s);
                                out("\x1b7");
                            } else {
                                flowFail("  No backtest with that id.");
                            }
                        }
                        state = .idle;
                        drawBar(input[0..0]);
                    },

                    // ── /montecarlo flow ────────────────────────────────────────
                    .awaiting_mc_pick => {
                        if (cmd.len > 0) {
                            const sel_id = std.fmt.parseInt(i64, cmd, 10) catch -1;
                            var found: ?db.BacktestEntry = null;
                            for (g_delete_entries[0..g_delete_count]) |entry| {
                                if (entry.id == sel_id) {
                                    found = entry;
                                    break;
                                }
                            }
                            if (found) |entry| {
                                runMonteCarlo(io, gpa, entry, sel_id);
                            } else {
                                flowFail("  No backtest with that id.");
                            }
                        }
                        state = .idle;
                        drawBar(input[0..0]);
                    },

                    // ── /combine flow ───────────────────────────────────────────
                    .awaiting_combine_balance => {
                        g_combine_balance = 1000.0;
                        if (!parseVolFloat(cmd, &g_combine_balance)) {
                            flowFail("  Invalid balance.");
                            state = .idle;
                            drawBar(input[0..0]);
                            continue;
                        }
                        g_combine_count = 0;
                        g_delete_count = db.list(&g_delete_entries) catch 0;
                        var usable: usize = 0;
                        for (g_delete_entries[0..g_delete_count]) |ve| {
                            if (combineEntryUsable(ve)) usable += 1;
                        }
                        var abuf: [128]u8 = undefined;
                        const answered = std.fmt.bufPrint(&abuf, "  Initial balance? ${d}", .{g_combine_balance}) catch "  Initial balance? ?";
                        if (usable == 0) {
                            out("\x1b8\r\x1b[2K");
                            out(answered);
                            out("\n\n  No saved runs with parameters. Run a strategy and save it first.\n\n");
                            out("\x1b7");
                            state = .idle;
                        } else {
                            out("\x1b8\r\x1b[2K");
                            out(answered);
                            out("\n");
                            out("\x1b7");
                            renderCombinePick();
                            state = .awaiting_combine_pick;
                        }
                        drawBar(input[0..0]);
                    },
                    .awaiting_combine_pick => {
                        // Echo the answer over the prompt line.
                        out("\x1b8\r\x1b[2K");
                        var eb: [64]u8 = undefined;
                        const echo = std.fmt.bufPrint(&eb, "  Select id: {s}\n", .{cmd}) catch "  Select id:\n";
                        out(echo);
                        out("\x1b7");

                        if (cmd.len == 0) {
                            if (g_combine_count == 0) {
                                printContent("  Nothing selected.\n\n");
                                state = .idle;
                            } else {
                                printContent("\n");
                                runCombine(io, gpa, g_combine_balance, g_combine_ids[0..g_combine_count]);
                                state = .idle;
                            }
                            drawBar(input[0..0]);
                            continue;
                        }

                        const sel_id = std.fmt.parseInt(i64, cmd, 10) catch -1;
                        var ok = false;
                        for (g_delete_entries[0..g_delete_count]) |entry| {
                            if (entry.id != sel_id or combineHasId(sel_id)) continue;
                            if (!combineEntryUsable(entry)) continue;
                            ok = true;
                            break;
                        }
                        if (!ok) {
                            printContent("  Invalid or already-picked id.\n");
                            renderCombinePick();
                            drawBar(input[0..0]);
                            continue;
                        }

                        g_combine_ids[g_combine_count] = sel_id;
                        g_combine_count += 1;

                        // Count usable entries to detect when all are picked.
                        var visible: usize = 0;
                        for (g_delete_entries[0..g_delete_count]) |ve| {
                            if (combineEntryUsable(ve)) visible += 1;
                        }
                        if (g_combine_count >= visible) {
                            printContent("\n");
                            runCombine(io, gpa, g_combine_balance, g_combine_ids[0..g_combine_count]);
                            state = .idle;
                        } else {
                            renderCombinePick();
                        }
                        drawBar(input[0..0]);
                    },
                }
            },

            // Backspace.
            127, 8 => {
                if (input_len > 0) input_len -= 1;
                drawBar(input[0..input_len]);
            },

            // Ctrl+C: cancel the current prompt and return to idle.
            3 => {
                switch (state) {
                    .idle => {},
                    .awaiting_strategy,
                    .awaiting_symbol,
                    .awaiting_instrument,
                    .awaiting_balance,
                    .awaiting_base_contracts,
                    .awaiting_leverage,
                    .awaiting_sizing,
                    .awaiting_vol_target,
                    .awaiting_vol_halflife,
                    .awaiting_vol_maxmult,
                    .awaiting_vol_mindays,
                    .awaiting_from,
                    .awaiting_spread,
                    .awaiting_slippage,
                    .awaiting_bh_balance,
                    .awaiting_bh_lots,
                    .awaiting_bh_from,
                    .awaiting_bh_spread,
                    .awaiting_bh_slippage,
                    .awaiting_tune_strategy,
                    .awaiting_tune_symbol,
                    .awaiting_tune_instrument,
                    .awaiting_tune_balance,
                    .awaiting_tune_base_contracts,
                    .awaiting_tune_leverage,
                    .awaiting_tune_sizing,
                    .awaiting_tune_vol_target,
                    .awaiting_tune_vol_halflife,
                    .awaiting_tune_vol_maxmult,
                    .awaiting_tune_vol_mindays,
                    .awaiting_tune_from,
                    .awaiting_tune_spread,
                    .awaiting_tune_slippage,
                    .awaiting_delete,
                    .awaiting_combine_balance,
                    .awaiting_combine_pick,
                    .awaiting_mc_pick,
                    => {
                        flowFail("  Cancelled.");
                        state = .idle;
                        input_len = 0;
                        drawBar(input[0..0]);
                    },
                }
            },

            // Tab: complete the highlighted command.
            '\t' => {
                if (input_len > 1 and input[0] == '/') {
                    for (&COMMANDS) |cmd| {
                        if (std.mem.startsWith(u8, cmd.name, input[0..input_len])) {
                            if (cmd.name.len <= input.len) {
                                @memcpy(input[0..cmd.name.len], cmd.name);
                                input_len = cmd.name.len;
                                drawBar(input[0..input_len]);
                            }
                            break;
                        }
                    }
                }
            },

            // Printable ASCII.
            32...126 => {
                if (input_len < input.len - 1) {
                    input[input_len] = ch[0];
                    input_len += 1;
                }
                drawBar(input[0..input_len]);
            },

            else => {},
        }
    }
}
