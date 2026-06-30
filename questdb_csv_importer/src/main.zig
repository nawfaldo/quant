const std = @import("std");
const Io = std.Io;
const net = std.Io.net;

// ── CLI usage ────────────────────────────────────────────────────────────────
// zig-out/bin/questdb_csv_importer <csv-file> [options]
//
//   --table   NAME        table name base      (default: csv filename stem)
//   --host    HOST        QuestDB host         (default: 127.0.0.1)
//   --port    PORT        ILP TCP port         (default: 9009)
//   --ts-col  NAME|N      date/timestamp col   (default: 0)
//   --ts-col2 NAME|N      time column to merge with --ts-col (optional)
//   --delim   CHAR        CSV delimiter        (default: ,)
//   --aggregate / -a      aggregate OHLCV data into _1m/_5m/_15m/_30m/_1h/_4h/_1d tables
// ─────────────────────────────────────────────────────────────────────────────

const BATCH_SIZE = 10_000;
const PROGRESS_INTERVAL = 50_000;
const MAX_COLS = 64;
const MAX_COL_NAME = 128;
const NUM_TF = 7;

// Timeframe definitions: minutes per bar and table name suffix
const TF_MINUTES = [NUM_TF]i64{ 1, 5, 15, 30, 60, 240, 1440 };
const TF_SUFFIXES = [NUM_TF][]const u8{ "1m", "5m", "15m", "30m", "1h", "4h", "1d" };

const ColType = enum { timestamp, float, integer, string };

const TzMode = enum { fixed, utc_to_et };

const OhlcvCols = struct {
    open: usize,
    high: usize,
    low: usize,
    close: usize,
    volume: ?usize,
};

const CandleState = struct {
    ts_ns: i64 = 0,
    open: f64 = 0,
    high: f64 = 0,
    low: f64 = 0,
    close: f64 = 0,
    volume: f64 = 0,
    active: bool = false,
};

const Config = struct {
    csv_path: []const u8 = "",
    table_name: []const u8 = "",
    host: []const u8 = "127.0.0.1",
    port: u16 = 9009,
    ts_col: usize = 0,
    ts_col2: ?usize = null, // when set, combined as "<ts_col> <ts_col2>" before parsing
    tz_offset_ns: i64 = 0, // nanoseconds to add to every parsed timestamp (fixed mode)
    tz_mode: TzMode = .fixed,
    delim: u8 = ',',
    aggregate: bool = false,
};

pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const gpa = init.gpa;

    var stderr_buf: [512]u8 = undefined;
    var err_w = Io.File.stderr().writer(io, &stderr_buf);

    var cfg = Config{};

    // ── argument parsing ─────────────────────────────────────────────────────
    var args_it = try std.process.Args.Iterator.initAllocator(init.minimal.args, gpa);
    defer args_it.deinit();
    _ = args_it.skip(); // argv[0]

    while (args_it.next()) |arg| {
        if (std.mem.eql(u8, arg, "--table")) {
            cfg.table_name = args_it.next() orelse fatal(&err_w, io, "--table requires a value");
        } else if (std.mem.eql(u8, arg, "--host")) {
            cfg.host = args_it.next() orelse fatal(&err_w, io, "--host requires a value");
        } else if (std.mem.eql(u8, arg, "--port")) {
            const s = args_it.next() orelse fatal(&err_w, io, "--port requires a value");
            cfg.port = std.fmt.parseInt(u16, s, 10) catch fatal(&err_w, io, "invalid --port value");
        } else if (std.mem.eql(u8, arg, "--ts-col")) {
            const s = args_it.next() orelse fatal(&err_w, io, "--ts-col requires a value");
            cfg.ts_col = std.fmt.parseInt(usize, s, 10) catch blk: {
                break :blk std.math.maxInt(usize); // sentinel: resolve by name later
            };
            if (cfg.ts_col == std.math.maxInt(usize)) {
                @memcpy(ts_col_name_buf[0..s.len], s);
                ts_col_name_len = s.len;
            }
        } else if (std.mem.eql(u8, arg, "--ts-col2")) {
            const s = args_it.next() orelse fatal(&err_w, io, "--ts-col2 requires a value");
            const idx = std.fmt.parseInt(usize, s, 10) catch blk: {
                @memcpy(ts_col2_name_buf[0..s.len], s);
                ts_col2_name_len = s.len;
                break :blk std.math.maxInt(usize); // sentinel: resolve by name later
            };
            cfg.ts_col2 = idx;
        } else if (std.mem.eql(u8, arg, "--delim")) {
            const s = args_it.next() orelse fatal(&err_w, io, "--delim requires a value");
            cfg.delim = if (s.len == 1) s[0] else fatal(&err_w, io, "--delim must be a single character");
        } else if (std.mem.eql(u8, arg, "--tz-hours")) {
            const s = args_it.next() orelse fatal(&err_w, io, "--tz-hours requires a value");
            const hours = std.fmt.parseInt(i64, s, 10) catch fatal(&err_w, io, "invalid --tz-hours value");
            cfg.tz_offset_ns = hours * 3_600_000_000_000;
        } else if (std.mem.eql(u8, arg, "--tz-et")) {
            cfg.tz_mode = .utc_to_et;
        } else if (std.mem.eql(u8, arg, "--aggregate") or std.mem.eql(u8, arg, "-a")) {
            cfg.aggregate = true;
        } else if (std.mem.startsWith(u8, arg, "--")) {
            try err_w.interface.print("Unknown flag: {s}\n", .{arg});
            try err_w.interface.flush();
            std.process.exit(1);
        } else {
            cfg.csv_path = arg;
        }
    }

    if (cfg.csv_path.len == 0) {
        try err_w.interface.writeAll(
            \\Usage: questdb_csv_importer <csv-file> [options]
            \\  --table   NAME     table name base (default: csv filename stem)
            \\  --host    HOST     QuestDB host (default: 127.0.0.1)
            \\  --port    PORT     ILP port (default: 9009)
            \\  --ts-col  NAME|N   date/timestamp column name or index (default: 0)
            \\  --ts-col2 NAME|N   time column to merge with --ts-col (e.g. separate date+time cols)
            \\  --delim   CHAR     CSV delimiter (default: ,)
            \\  --tz-hours  N      shift timestamps by N hours (e.g. 1 for CT→ET)
            \\  --tz-et            DST-aware UTC → New York ET conversion
            \\  --aggregate / -a   aggregate OHLCV into _1m/_5m/_15m/_30m/_1h/_4h/_1d tables
            \\
        );
        try err_w.interface.flush();
        std.process.exit(1);
    }

    // default table name = filename stem
    if (cfg.table_name.len == 0) {
        const base = std.fs.path.basename(cfg.csv_path);
        const stem = if (std.mem.lastIndexOf(u8, base, ".")) |dot| base[0..dot] else base;
        cfg.table_name = stem;
    }

    // ── open CSV ─────────────────────────────────────────────────────────────
    try err_w.interface.print("Opening: {s}\n", .{cfg.csv_path});
    try err_w.interface.flush();

    const csv_file = Io.Dir.cwd().openFile(io, cfg.csv_path, .{}) catch |e| {
        try err_w.interface.print("Cannot open file: {t}\n", .{e});
        try err_w.interface.flush();
        std.process.exit(1);
    };
    defer csv_file.close(io);

    var file_read_buf: [131072]u8 = undefined;
    var file_reader = csv_file.reader(io, &file_read_buf);

    // ── read header ──────────────────────────────────────────────────────────
    const header_line = (try file_reader.interface.takeDelimiter('\n')) orelse
        fatal(&err_w, io, "CSV file is empty");

    const header = std.mem.trimEnd(u8, header_line, "\r");

    var col_name_storage: [MAX_COLS * MAX_COL_NAME]u8 = undefined;
    var col_names: [MAX_COLS][]const u8 = undefined;
    var col_count: usize = 0;
    var storage_used: usize = 0;
    {
        var it = std.mem.splitScalar(u8, header, cfg.delim);
        while (it.next()) |raw| : (col_count += 1) {
            if (col_count >= MAX_COLS) break;
            const trimmed = std.mem.trim(u8, raw, " \t");
            // strip surrounding double-quotes or angle brackets (e.g. "Open"→Open, <DATE>→DATE)
            const unquoted = if (trimmed.len >= 2 and trimmed[0] == '"' and trimmed[trimmed.len - 1] == '"')
                trimmed[1 .. trimmed.len - 1]
            else if (trimmed.len >= 2 and trimmed[0] == '<' and trimmed[trimmed.len - 1] == '>')
                trimmed[1 .. trimmed.len - 1]
            else
                trimmed;
            const dest_start = storage_used;
            const copy_len = @min(unquoted.len, MAX_COL_NAME - 1);
            @memcpy(col_name_storage[dest_start .. dest_start + copy_len], unquoted[0..copy_len]);
            col_names[col_count] = col_name_storage[dest_start .. dest_start + copy_len];
            storage_used += copy_len;
        }
    }

    // resolve ts-col by name if needed
    if (ts_col_name_len > 0) {
        const name = ts_col_name_buf[0..ts_col_name_len];
        var found = false;
        for (col_names[0..col_count], 0..) |cn, i| {
            if (std.mem.eql(u8, cn, name)) {
                cfg.ts_col = i;
                found = true;
                break;
            }
        }
        if (!found) {
            try err_w.interface.print("Column '{s}' not found in header\n", .{name});
            try err_w.interface.flush();
            std.process.exit(1);
        }
    }

    if (cfg.ts_col >= col_count) fatal(&err_w, io, "ts-col index out of range");

    // resolve ts-col2 by name if needed
    if (ts_col2_name_len > 0) {
        const name = ts_col2_name_buf[0..ts_col2_name_len];
        var found = false;
        for (col_names[0..col_count], 0..) |cn, i| {
            if (std.mem.eql(u8, cn, name)) {
                cfg.ts_col2 = i;
                found = true;
                break;
            }
        }
        if (!found) {
            try err_w.interface.print("Column '{s}' not found in header\n", .{name});
            try err_w.interface.flush();
            std.process.exit(1);
        }
    }
    if (cfg.ts_col2) |tc2| {
        if (tc2 >= col_count) fatal(&err_w, io, "ts-col2 index out of range");
    }

    // ── sniff types from first data row ──────────────────────────────────────
    var col_types: [MAX_COLS]ColType = undefined;
    {
        const peek = (try file_reader.interface.takeDelimiter('\n')) orelse
            fatal(&err_w, io, "CSV has header but no data rows");
        const peeked = std.mem.trimEnd(u8, peek, "\r");

        var it = std.mem.splitScalar(u8, peeked, cfg.delim);
        var ci: usize = 0;
        while (it.next()) |val| : (ci += 1) {
            if (ci >= col_count) break;
            const is_ts = ci == cfg.ts_col or (cfg.ts_col2 != null and ci == cfg.ts_col2.?);
            col_types[ci] = if (is_ts) .timestamp else inferType(val);
        }

        @memcpy(first_row_buf[0..peeked.len], peeked);
        first_row_len = peeked.len;
    }

    try err_w.interface.print("Table: {s}  Columns: {d}  Timestamp col: {d} ({s})\n", .{
        cfg.table_name, col_count, cfg.ts_col, col_names[cfg.ts_col],
    });
    try err_w.interface.flush();

    // ── connect to QuestDB ───────────────────────────────────────────────────
    try err_w.interface.print("Connecting to {s}:{d}...\n", .{ cfg.host, cfg.port });
    try err_w.interface.flush();

    const host_buf = try gpa.alloc(u8, cfg.host.len + 1);
    defer gpa.free(host_buf);
    @memcpy(host_buf[0..cfg.host.len], cfg.host);
    host_buf[cfg.host.len] = 0;

    const addr = net.IpAddress.parseIp4(cfg.host, cfg.port) catch fatal(&err_w, io, "invalid host address");
    var stream = addr.connect(io, .{ .mode = .stream }) catch |e| {
        try err_w.interface.print("Connection failed: {t}\n", .{e});
        try err_w.interface.flush();
        std.process.exit(1);
    };
    defer stream.close(io);

    try err_w.interface.print("Connected.\n", .{});
    try err_w.interface.flush();

    var tcp_write_buf: [262144]u8 = undefined;
    var tcp_writer = stream.writer(io, &tcp_write_buf);

    // ── aggregate mode ───────────────────────────────────────────────────────
    if (cfg.aggregate) {
        const ohlcv = detectOhlcv(col_names[0..col_count]) orelse {
            try err_w.interface.writeAll(
                "Aggregate mode requires open/high/low/close columns in the CSV.\n",
            );
            try err_w.interface.flush();
            std.process.exit(1);
        };

        // Strip any existing timeframe suffix from the base name so that
        // passing nq_1m.csv still produces nq_1m, nq_5m, … (not nq_1m_1m).
        var base_name = cfg.table_name;
        for ([_][]const u8{ "_1m", "_5m", "_15m", "_30m", "_1h", "_4h", "_1d" }) |suf| {
            if (std.mem.endsWith(u8, base_name, suf)) {
                base_name = base_name[0 .. base_name.len - suf.len];
                break;
            }
        }

        var tf_name_bufs: [NUM_TF][256]u8 = undefined;
        var tf_names: [NUM_TF][]const u8 = undefined;
        for (TF_SUFFIXES, 0..) |suffix, i| {
            tf_names[i] = std.fmt.bufPrint(
                &tf_name_bufs[i],
                "{s}_{s}",
                .{ base_name, suffix },
            ) catch fatal(&err_w, io, "table name too long");
        }

        try err_w.interface.print("Aggregating into tables:", .{});
        for (tf_names) |tn| try err_w.interface.print("  {s}", .{tn});
        try err_w.interface.print("\n", .{});
        try err_w.interface.flush();

        var candles = std.mem.zeroes([NUM_TF]CandleState);
        var row_count: u64 = 0;
        var skip_count: u64 = 0;
        var batch_rows: u64 = 0;

        // process the peeked first row
        if (aggProcessRow(
            first_row_buf[0..first_row_len],
            &cfg, col_count, ohlcv,
            &candles, &tf_names,
            &tcp_writer.interface, &batch_rows,
        )) |_| {
            row_count += 1;
        } else |_| {
            skip_count += 1;
        }

        // main aggregate loop
        while (true) {
            const maybe = file_reader.interface.takeDelimiter('\n') catch break;
            const line = maybe orelse break;
            const trimmed = std.mem.trimEnd(u8, line, "\r");
            if (trimmed.len == 0) continue;

            if (aggProcessRow(
                trimmed,
                &cfg, col_count, ohlcv,
                &candles, &tf_names,
                &tcp_writer.interface, &batch_rows,
            )) |_| {
                row_count += 1;
            } else |_| {
                skip_count += 1;
                continue;
            }

            if (batch_rows >= BATCH_SIZE) {
                try tcp_writer.interface.flush();
                batch_rows = 0;
            }

            if (row_count % PROGRESS_INTERVAL == 0 and row_count > 0) {
                try err_w.interface.print("\r  {d} input rows processed...", .{row_count});
                try err_w.interface.flush();
            }
        }

        // flush remaining open candles (the last incomplete bar for each timeframe)
        for (0..NUM_TF) |i| {
            if (candles[i].active) {
                writeCandle(tf_names[i], candles[i], &tcp_writer.interface) catch {};
            }
        }
        try tcp_writer.interface.flush();

        try err_w.interface.print("\nDone. Input rows: {d}  Skipped: {d}\n", .{ row_count, skip_count });
        try err_w.interface.flush();
        return;
    }

    // ── raw import loop ──────────────────────────────────────────────────────
    var row_count: u64 = 0;
    var skip_count: u64 = 0;
    var batch_rows: u64 = 0;

    // process the peeked first row
    {
        const row = first_row_buf[0..first_row_len];
        writeRow(row, &cfg, col_names[0..col_count], col_types[0..col_count], &tcp_writer.interface) catch {
            skip_count += 1;
        };
        if (skip_count == 0) { row_count += 1; batch_rows += 1; }
    }

    while (true) {
        const maybe_line = file_reader.interface.takeDelimiter('\n') catch break;
        const line = maybe_line orelse break;

        const trimmed = std.mem.trimEnd(u8, line, "\r");
        if (trimmed.len == 0) continue;

        writeRow(trimmed, &cfg, col_names[0..col_count], col_types[0..col_count], &tcp_writer.interface) catch {
            skip_count += 1;
            continue;
        };

        row_count += 1;
        batch_rows += 1;

        if (batch_rows >= BATCH_SIZE) {
            try tcp_writer.interface.flush();
            batch_rows = 0;
        }

        if (row_count % PROGRESS_INTERVAL == 0) {
            try err_w.interface.print("\r  {d} rows inserted...", .{row_count});
            try err_w.interface.flush();
        }
    }

    if (batch_rows > 0) try tcp_writer.interface.flush();

    try err_w.interface.print("\nDone. Inserted: {d}  Skipped: {d}\n", .{ row_count, skip_count });
    try err_w.interface.flush();
}

// ── aggregate helpers ─────────────────────────────────────────────────────────

fn detectOhlcv(col_names: []const []const u8) ?OhlcvCols {
    var open_i: ?usize = null;
    var high_i: ?usize = null;
    var low_i: ?usize = null;
    var close_i: ?usize = null;
    var vol_i: ?usize = null;

    for (col_names, 0..) |name, i| {
        if (std.ascii.eqlIgnoreCase(name, "open")) open_i = i
        else if (std.ascii.eqlIgnoreCase(name, "high")) high_i = i
        else if (std.ascii.eqlIgnoreCase(name, "low")) low_i = i
        else if (std.ascii.eqlIgnoreCase(name, "close")) close_i = i
        else if (std.ascii.eqlIgnoreCase(name, "volume") or
                 std.ascii.eqlIgnoreCase(name, "vol")) vol_i = i;
    }

    if (open_i == null or high_i == null or low_i == null or close_i == null) return null;
    return .{ .open = open_i.?, .high = high_i.?, .low = low_i.?, .close = close_i.?, .volume = vol_i };
}

// Floor ts_ns to the start of a bar of `minutes` width (UTC-anchored).
fn barStartNs(ts_ns: i64, minutes: i64) i64 {
    const bar_ns: i64 = minutes * 60 * 1_000_000_000;
    return @divFloor(ts_ns, bar_ns) * bar_ns;
}

fn writeCandle(table_name: []const u8, state: CandleState, writer: *Io.Writer) !void {
    try writer.print(
        "{s} open={d},high={d},low={d},close={d},volume={d}i {d}\n",
        .{
            table_name,
            state.open,
            state.high,
            state.low,
            state.close,
            @as(i64, @intFromFloat(@round(state.volume))),
            state.ts_ns,
        },
    );
}

// Process one input row in aggregate mode: update all timeframe candles,
// flushing the previous candle whenever a bar boundary is crossed.
fn aggProcessRow(
    line: []const u8,
    cfg: *const Config,
    col_count: usize,
    ohlcv: OhlcvCols,
    candles: *[NUM_TF]CandleState,
    tf_names: *const [NUM_TF][]const u8,
    writer: *Io.Writer,
    batch_rows: *u64,
) !void {
    var vals: [MAX_COLS][]const u8 = undefined;
    var vc: usize = 0;
    var it = std.mem.splitScalar(u8, line, cfg.delim);
    while (it.next()) |v| : (vc += 1) {
        if (vc >= MAX_COLS) break;
        vals[vc] = v;
    }
    if (vc < col_count) return error.TooFewColumns;

    const ts_ns = try parseTs(cfg, &vals);
    const open  = try std.fmt.parseFloat(f64, std.mem.trim(u8, vals[ohlcv.open],  " \t\r"));
    const high  = try std.fmt.parseFloat(f64, std.mem.trim(u8, vals[ohlcv.high],  " \t\r"));
    const low   = try std.fmt.parseFloat(f64, std.mem.trim(u8, vals[ohlcv.low],   " \t\r"));
    const close = try std.fmt.parseFloat(f64, std.mem.trim(u8, vals[ohlcv.close], " \t\r"));
    const volume: f64 = if (ohlcv.volume) |vi|
        std.fmt.parseFloat(f64, std.mem.trim(u8, vals[vi], " \t\r")) catch 0
    else
        0;

    for (0..NUM_TF) |i| {
        const bar_ts = barStartNs(ts_ns, TF_MINUTES[i]);
        const c = &candles[i];

        // bar boundary crossed: emit the completed candle
        if (c.active and c.ts_ns != bar_ts) {
            try writeCandle(tf_names[i], c.*, writer);
            batch_rows.* += 1;
            c.active = false;
        }

        if (c.active) {
            // same bar: merge — first open stays, extend high/low, update close
            if (high > c.high) c.high = high;
            if (low  < c.low)  c.low  = low;
            c.close  = close;
            c.volume += volume;
        } else {
            // new bar
            c.* = .{
                .ts_ns  = bar_ts,
                .open   = open,
                .high   = high,
                .low    = low,
                .close  = close,
                .volume = volume,
                .active = true,
            };
        }
    }
}

// ── row writer ────────────────────────────────────────────────────────────────

fn writeRow(
    line: []const u8,
    cfg: *const Config,
    col_names: []const []const u8,
    col_types: []const ColType,
    writer: *Io.Writer,
) !void {
    var vals: [MAX_COLS][]const u8 = undefined;
    var vc: usize = 0;
    var it = std.mem.splitScalar(u8, line, cfg.delim);
    while (it.next()) |v| : (vc += 1) {
        if (vc >= col_types.len) break;
        vals[vc] = v;
    }
    if (vc < col_types.len) return error.TooFewColumns;

    const ts_ns = try parseTs(cfg, &vals);

    try writer.writeAll(cfg.table_name);
    try writer.writeByte(' ');

    var first_field = true;
    var field_count: usize = 0;
    for (col_names, col_types, 0..) |name, ctype, ci| {
        if (ci == cfg.ts_col) continue;
        if (cfg.ts_col2 != null and ci == cfg.ts_col2.?) continue;
        const raw = std.mem.trim(u8, vals[ci], " \t\r");
        if (raw.len == 0) continue;

        if (!first_field) try writer.writeByte(',');
        first_field = false;
        field_count += 1;

        try writer.writeAll(name);
        try writer.writeByte('=');

        switch (ctype) {
            .integer => {
                const v = parseIntOrFloat(raw) catch return error.BadValue;
                try writer.print("{d}i", .{v});
            },
            .float => {
                const v = std.fmt.parseFloat(f64, raw) catch return error.BadValue;
                if (preferInteger(name)) {
                    try writer.print("{d}i", .{@as(i64, @intFromFloat(@round(v)))});
                } else {
                    try writer.print("{d}", .{v});
                }
            },
            .string => {
                try writer.writeByte('"');
                try writer.writeAll(raw);
                try writer.writeByte('"');
            },
            .timestamp => unreachable,
        }
    }

    if (field_count == 0) return error.NoFields;
    try writer.print(" {d}\n", .{ts_ns});
}

// ── type inference ────────────────────────────────────────────────────────────

fn inferType(val: []const u8) ColType {
    const v = std.mem.trim(u8, val, " \t\r");
    if (std.fmt.parseFloat(f64, v)) |_| return .float else |_| {}
    return .string;
}

fn preferInteger(col_name: []const u8) bool {
    const lower = col_name;
    return std.mem.eql(u8, lower, "volume") or
        std.mem.eql(u8, lower, "Volume") or
        std.mem.endsWith(u8, lower, "_count") or
        std.mem.endsWith(u8, lower, "_cnt") or
        std.mem.endsWith(u8, lower, "_qty");
}

// Combine ts_col (and optionally ts_col2) from parsed row values into a
// nanosecond timestamp.  When ts_col2 is set, the two cell values are joined
// with a space before parsing, e.g. "11/12/2008" + "02:17" → "11/12/2008 02:17".
fn parseTs(cfg: *const Config, vals: *const [MAX_COLS][]const u8) !i64 {
    const date_raw = std.mem.trim(u8, vals[cfg.ts_col], " \t\r");
    const ts = if (cfg.ts_col2) |tc2| blk: {
        const time_raw = std.mem.trim(u8, vals[tc2], " \t\r");
        var buf: [64]u8 = undefined;
        const combined = std.fmt.bufPrint(&buf, "{s} {s}", .{ date_raw, time_raw }) catch
            return error.TimestampTooLong;
        break :blk try parseTimestamp(combined);
    } else try parseTimestamp(date_raw);
    return switch (cfg.tz_mode) {
        .fixed => ts + cfg.tz_offset_ns,
        .utc_to_et => ts + etOffsetNs(ts),
    };
}

// ── timestamp parsing ─────────────────────────────────────────────────────────
//
// Supports:
//   Unix integer (all digits)         → nanoseconds/microseconds/milliseconds/seconds
//   D/M/YYYY H:MM                     → slash format (day-first)
//   YYYY-MM-DD HH:MM[:SS[.ffffff]]    → ISO-like with space
//   YYYY-MM-DDTHH:MM[:SS[.ffffff]]    → ISO 8601

fn parseTimestamp(s: []const u8) !i64 {
    if (isAllDigits(s)) {
        const v = try std.fmt.parseInt(i64, s, 10);
        return scaleToNanos(v);
    }

    if (std.mem.indexOfScalar(u8, s, '/') != null) {
        var i: usize = 0;
        const first  = try parseNextInt(u32, s, &i, '/');
        const second = try parseNextInt(u32, s, &i, '/');
        const year   = try parseNextInt(i32, s, &i, ' ');
        const hour   = try parseNextInt(u32, s, &i, ':');
        const minute = try parseDigits(u32, s, &i);
        // This dataset is day-first (D/M/YYYY). Disambiguate off whichever field
        // is unambiguous (> 12 must be the day), and for genuinely ambiguous
        // dates (both ≤ 12) default to day-first. NOTE: the previous version
        // defaulted ambiguous dates to month-first, which silently swapped the
        // day and month on every row with day ≤ 12 (e.g. 11/12/2008 → 12 Nov).
        const day   = if (second > 12) second else first;
        const month = if (second > 12) first else second;
        return (try dateTimeToUnix(year, month, day, hour, minute, 0)) * 1_000_000_000;
    }

    // YYYY.MM.DD[ HH:MM[:SS[.mmm]]] — e.g. "2005.01.03 00:01" or "2026.01.01 23:00:02.147"
    if (s.len >= 10 and s[4] == '.') {
        const year   = try std.fmt.parseInt(i32, s[0..4],  10);
        const month  = try std.fmt.parseInt(u32, s[5..7],  10);
        const day    = try std.fmt.parseInt(u32, s[8..10], 10);
        var hour: u32 = 0;
        var minute: u32 = 0;
        var second: u32 = 0;
        var millis: i64 = 0;
        if (s.len >= 16 and s[10] == ' ') {
            hour   = try std.fmt.parseInt(u32, s[11..13], 10);
            minute = try std.fmt.parseInt(u32, s[14..16], 10);
            if (s.len >= 19 and s[16] == ':') {
                second = try std.fmt.parseInt(u32, s[17..19], 10);
                if (s.len >= 23 and s[19] == '.') {
                    millis = try std.fmt.parseInt(i64, s[20..23], 10);
                }
            }
        }
        const unix = try dateTimeToUnix(year, month, day, hour, minute, second);
        return unix * 1_000_000_000 + millis * 1_000_000;
    }

    if (s.len >= 16 and s[4] == '-') {
        const year   = try std.fmt.parseInt(i32, s[0..4],   10);
        const month  = try std.fmt.parseInt(u32, s[5..7],   10);
        const day    = try std.fmt.parseInt(u32, s[8..10],  10);
        const hour   = try std.fmt.parseInt(u32, s[11..13], 10);
        const minute = try std.fmt.parseInt(u32, s[14..16], 10);
        var second: u32 = 0;
        var micros: i64 = 0;
        if (s.len > 17 and s[16] == ':') {
            second = try std.fmt.parseInt(u32, s[17..19], 10);
            if (s.len > 20 and s[19] == '.') {
                var frac = s[20..];
                if (frac.len > 6) frac = frac[0..6];
                const fv = try std.fmt.parseInt(i64, frac, 10);
                var pad: i64 = 1;
                var p: usize = frac.len;
                while (p < 6) : (p += 1) pad *= 10;
                micros = fv * pad;
            }
        }
        const unix = try dateTimeToUnix(year, month, day, hour, minute, second);
        return unix * 1_000_000_000 + micros * 1000;
    }

    return error.UnknownTimestampFormat;
}

fn scaleToNanos(v: i64) i64 {
    if (v > 1_000_000_000_000_000) return v;           // already ns
    if (v > 1_000_000_000_000)     return v * 1_000;   // us → ns
    if (v > 1_000_000_000)         return v * 1_000_000; // ms → ns
    return v * 1_000_000_000;                           // s  → ns
}

fn isAllDigits(s: []const u8) bool {
    if (s.len == 0) return false;
    for (s) |c| if (c < '0' or c > '9') return false;
    return true;
}

// ── US Eastern DST helpers ────────────────────────────────────────────────────
// Converts a UTC nanosecond timestamp to the appropriate ET offset (nanoseconds).
// EDT (UTC-4): 2nd Sunday of March at 07:00 UTC → 1st Sunday of November at 06:00 UTC
// EST (UTC-5): everything else
fn etOffsetNs(utc_ns: i64) i64 {
    const utc_s = @divFloor(utc_ns, 1_000_000_000);
    const year = utcSecondsYear(utc_s);
    const edt_start = nthWeekdayUnixSecs(year, 3, 0, 2, 7) catch return -5 * 3_600_000_000_000;
    const est_start = nthWeekdayUnixSecs(year, 11, 0, 1, 6) catch return -5 * 3_600_000_000_000;
    return if (utc_s >= edt_start and utc_s < est_start)
        -4 * 3_600_000_000_000
    else
        -5 * 3_600_000_000_000;
}

// Returns the year containing a given UTC second offset.
fn utcSecondsYear(utc_s: i64) i32 {
    var year: i32 = 1970;
    var rem = utc_s;
    while (true) {
        const secs: i64 = (if (isLeapYear(year)) @as(i64, 366) else @as(i64, 365)) * 86400;
        if (rem < secs) break;
        rem -= secs;
        year += 1;
    }
    return year;
}

// Returns the Unix second for the Nth occurrence (1-based) of weekday (0=Sun..6=Sat)
// in the given year/month at the given UTC hour.
fn nthWeekdayUnixSecs(year: i32, month: u32, weekday: u32, n: u32, hour: u32) !i64 {
    const first_secs = try dateTimeToUnix(year, month, 1, 0, 0, 0);
    const first_day_num: i64 = @divFloor(first_secs, 86400);
    // 1970-01-01 was Thursday = weekday 4
    const first_dow: i64 = @mod(first_day_num + 4, 7);
    const days_ahead: i64 = @mod(@as(i64, weekday) - first_dow + 7, 7);
    const day: u32 = @intCast(1 + days_ahead + @as(i64, (n - 1) * 7));
    return try dateTimeToUnix(year, month, day, hour, 0, 0);
}

// ── date/time helpers ─────────────────────────────────────────────────────────

fn parseNextInt(comptime T: type, s: []const u8, i: *usize, delim: u8) !T {
    const start = i.*;
    while (i.* < s.len and s[i.*] != delim) i.* += 1;
    const val = try std.fmt.parseInt(T, s[start..i.*], 10);
    if (i.* < s.len) i.* += 1;
    return val;
}

fn parseDigits(comptime T: type, s: []const u8, i: *usize) !T {
    const start = i.*;
    while (i.* < s.len and s[i.*] >= '0' and s[i.*] <= '9') i.* += 1;
    return std.fmt.parseInt(T, s[start..i.*], 10);
}

fn parseIntOrFloat(s: []const u8) !i64 {
    return std.fmt.parseInt(i64, s, 10) catch {
        const f = try std.fmt.parseFloat(f64, s);
        return @intFromFloat(@round(f));
    };
}

fn dateTimeToUnix(year: i32, month: u32, day: u32, hour: u32, minute: u32, second: u32) !i64 {
    if (month < 1 or month > 12 or day < 1 or day > 31) return error.InvalidDate;
    const dim = [12]u32{ 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
    var days: i64 = 0;
    var y: i32 = 1970;
    while (y < year) : (y += 1) days += if (isLeapYear(y)) 366 else 365;
    var m: u32 = 1;
    while (m < month) : (m += 1) {
        days += dim[m - 1];
        if (m == 2 and isLeapYear(year)) days += 1;
    }
    days += @as(i64, @intCast(day)) - 1;
    return days * 86400 + @as(i64, @intCast(hour)) * 3600 + @as(i64, @intCast(minute)) * 60 + @as(i64, @intCast(second));
}

fn isLeapYear(y: i32) bool {
    return (@rem(y, 4) == 0 and @rem(y, 100) != 0) or @rem(y, 400) == 0;
}

// ── misc ──────────────────────────────────────────────────────────────────────

var ts_col_name_buf: [MAX_COL_NAME]u8 = undefined;
var ts_col_name_len: usize = 0;
var ts_col2_name_buf: [MAX_COL_NAME]u8 = undefined;
var ts_col2_name_len: usize = 0;
var first_row_buf: [4096]u8 = undefined;
var first_row_len: usize = 0;

fn fatal(w: *Io.File.Writer, io: Io, msg: []const u8) noreturn {
    w.interface.writeAll(msg) catch {};
    w.interface.writeByte('\n') catch {};
    w.interface.flush() catch {};
    _ = io;
    std.process.exit(1);
}
