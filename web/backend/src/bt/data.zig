const std = @import("std");
const Io = std.Io;
const net = std.Io.net;

// One OHLCV (Open/High/Low/Close/Volume) bar. Every field has a default, so
// you can write `Bar{}` for an all-zero bar.
pub const Bar = struct {
    open: f64 = 0,
    high: f64 = 0,
    low: f64 = 0,
    close: f64 = 0,
    volume: i64 = 0,
};

// "YYYY-MM-DD HH:MM" is exactly 16 ASCII bytes.
pub const Ts = [16]u8;

// A dataset owns two parallel slices: bars and timestamps at the same index.
pub const Dataset = struct {
    bars: []Bar,
    timestamps: []Ts,
    allocator: std.mem.Allocator,

    pub fn deinit(self: Dataset) void {
        self.allocator.free(self.bars);
        self.allocator.free(self.timestamps);
    }
};

// Which columns does the strategy want? The engine fills this in from the
// strategy's compile-time `columns` decl.
pub const Columns = struct {
    open: bool = true,
    high: bool = true,
    low: bool = true,
    close: bool = true,
    volume: bool = true,
};

// QuestDB source descriptor.
pub const Source = struct {
    table: []const u8,
    from: ?[]const u8 = null,
    to: ?[]const u8 = null,
};

// --- QuestDB connection target ---------------------------------------------
//
// QuestDB's PostgreSQL-wire endpoint. Default install: admin/quest on port
// 8812 against database `qdb`. Override these if your install differs.
const PG_HOST = "127.0.0.1";
const PG_PORT: u16 = 8812;
const PG_USER = "admin";
const PG_PASS = "quest";
const PG_DB = "qdb";

// PostgreSQL binary timestamps are microseconds since 2000-01-01 UTC, not
// the unix epoch. This is the offset (in seconds) you add to convert.
const PG_EPOCH_UNIX: i64 = 946_684_800;

// =========================================================================
// fetch — open a PGWire connection, run the SELECT, decode binary rows.
//
// Why PGWire binary instead of /exp CSV?
//   * floats come back as 8 raw IEEE-754 bytes — zero text→f64 parsing
//   * ints come back as 8 raw bytes — zero atoi
//   * timestamps come back as a single int64 — zero string slicing
// Per QuestDB's docs, this is the recommended fastest path for large result
// sets. End-to-end this typically beats the CSV client by 3-6× on multi-
// million-bar fetches because parsing — not network — dominates that path.
// =========================================================================
pub fn fetch(io: Io, gpa: std.mem.Allocator, cols: Columns, src: Source) !Dataset {
    var sql_buf: [512]u8 = undefined;
    const sql = try buildSql(&sql_buf, cols, src);

    const addr = net.IpAddress.parseIp4(PG_HOST, PG_PORT) catch return error.InvalidAddress;
    var stream = try addr.connect(io, .{ .mode = .stream });
    defer stream.close(io);

    var write_buf: [4096]u8 = undefined;
    var writer = stream.writer(io, &write_buf);
    const w = &writer.interface;

    // 16 MB read buffer keeps syscalls down on large fetches.
    const read_buf = try gpa.alloc(u8, 16 * 1024 * 1024);
    defer gpa.free(read_buf);
    var reader = stream.reader(io, read_buf);
    const r = &reader.interface;

    try sendStartup(w);
    try w.flush();
    try handleAuth(r, w);
    try waitForReady(r);

    try sendExtendedQuery(w, sql);
    try w.flush();

    return try readResults(gpa, r, cols);
}

// =========================================================================
// streamFxTicks — fetch (timestamp, BID, ASK) rows from the fx_nq_ticks tick
// table and hand each one to `consumer.onTick(ts_unix_micros, bid, ask)` as it
// is decoded, WITHOUT buffering the whole result. The tick table has tens of
// millions of rows, so the re-pricer (bt/fx.zig) streams a single ordered pass
// and keeps only O(trades) state instead of materializing every tick.
//
// `from`/`to` are "YYYY-MM-DD" bounds (>= from, < to). `consumer` is any value
// with a `pub fn onTick(self: *@This(), ts: i64, bid: f64, ask: f64) void`.
// Timestamps are returned as unix microseconds (fake-UTC, matching the bar
// tables' clock) so callers can compare against bar timestamps directly.
// =========================================================================
pub fn streamFxTicks(io: Io, gpa: std.mem.Allocator, from: []const u8, to: []const u8, consumer: anytype) !void {
    var sql_buf: [256]u8 = undefined;
    const sql = std.fmt.bufPrint(&sql_buf, "SELECT timestamp, BID, ASK FROM fx_nq_ticks WHERE timestamp >= '{s}' AND timestamp < '{s}' ORDER BY timestamp", .{ from, to }) catch return error.SqlTooLong;

    const addr = net.IpAddress.parseIp4(PG_HOST, PG_PORT) catch return error.InvalidAddress;
    var stream = try addr.connect(io, .{ .mode = .stream });
    defer stream.close(io);

    var write_buf: [4096]u8 = undefined;
    var writer = stream.writer(io, &write_buf);
    const w = &writer.interface;

    const read_buf = try gpa.alloc(u8, 16 * 1024 * 1024);
    defer gpa.free(read_buf);
    var reader = stream.reader(io, read_buf);
    const r = &reader.interface;

    try sendStartup(w);
    try w.flush();
    try handleAuth(r, w);
    try waitForReady(r);
    try sendExtendedQuery(w, sql);
    try w.flush();

    while (true) {
        const h = try readHeader(r);
        switch (h.tag) {
            '1', '2', 'T', 'C' => try r.discardAll(h.payload_len),
            'D' => {
                const col_count = try r.takeInt(u16, .big);
                if (col_count < 3) {
                    // Drain whatever fields exist and skip this row.
                    var k: u16 = 0;
                    while (k < col_count) : (k += 1) {
                        const flen = try r.takeInt(i32, .big);
                        if (flen > 0) try r.discardAll(@intCast(flen));
                    }
                    continue;
                }
                const ts = try readTsMicros(r);
                const bid = try readF8(r);
                const ask = try readF8(r);
                var k: u16 = 3;
                while (k < col_count) : (k += 1) {
                    const flen = try r.takeInt(i32, .big);
                    if (flen > 0) try r.discardAll(@intCast(flen));
                }
                consumer.onTick(ts, bid, ask);
            },
            'Z' => {
                try r.discardAll(h.payload_len);
                return;
            },
            'E' => {
                try r.discardAll(h.payload_len);
                return error.PgwireServerError;
            },
            else => try r.discardAll(h.payload_len),
        }
    }
}

// Read a binary timestamp field as unix microseconds (fake-UTC). PG sends
// microseconds since 2000-01-01; we shift to the unix epoch so the value lines
// up with bar timestamps converted via the same civil-date math.
fn readTsMicros(r: *Io.Reader) !i64 {
    const len = try r.takeInt(i32, .big);
    if (len == -1) return 0;
    if (len != 8) return error.PgwireBadFieldLen;
    const micros = try r.takeInt(i64, .big);
    return micros + PG_EPOCH_UNIX * 1_000_000;
}

// =========================================================================
// Outbound protocol messages.
//
// Postgres frames look like: [1-byte type tag][int32 length including the
// length field itself][payload]. StartupMessage is the one exception — no
// type tag, just length + protocol + params.
// =========================================================================

fn sendStartup(w: *Io.Writer) !void {
    // Null-terminated key/value pairs, followed by a final null.
    const params = "user\x00" ++ PG_USER ++ "\x00database\x00" ++ PG_DB ++ "\x00\x00";
    const len: u32 = @intCast(4 + 4 + params.len);
    var hdr: [8]u8 = undefined;
    std.mem.writeInt(u32, hdr[0..4], len, .big);
    std.mem.writeInt(u32, hdr[4..8], 196_608, .big); // protocol 3.0
    try w.writeAll(&hdr);
    try w.writeAll(params);
}

fn sendMsg(w: *Io.Writer, tag: u8, payload: []const u8) !void {
    var hdr: [5]u8 = undefined;
    hdr[0] = tag;
    std.mem.writeInt(u32, hdr[1..5], @intCast(payload.len + 4), .big);
    try w.writeAll(&hdr);
    try w.writeAll(payload);
}

// PasswordMessage 'p' + password + 0x00. Used for both Cleartext and the
// MD5 auth response ("md5" + 32 hex chars).
fn sendPassword(w: *Io.Writer, password: []const u8) !void {
    var hdr: [5]u8 = undefined;
    hdr[0] = 'p';
    std.mem.writeInt(u32, hdr[1..5], @intCast(password.len + 1 + 4), .big);
    try w.writeAll(&hdr);
    try w.writeAll(password);
    try w.writeByte(0);
}

// One-shot extended-query: Parse + Bind + Describe + Execute + Sync.
// All names are empty strings (the "unnamed" statement and portal), which
// is the simplest valid form and is reused on every fetch.
fn sendExtendedQuery(w: *Io.Writer, sql: []const u8) !void {
    // Parse 'P': stmt_name\0 | query\0 | int16 num_param_types (=0)
    {
        const len: u32 = @intCast(4 + 1 + sql.len + 1 + 2);
        var hdr: [5]u8 = undefined;
        hdr[0] = 'P';
        std.mem.writeInt(u32, hdr[1..5], len, .big);
        try w.writeAll(&hdr);
        try w.writeByte(0);
        try w.writeAll(sql);
        try w.writeByte(0);
        try w.writeAll(&[_]u8{ 0, 0 });
    }

    // Bind 'B': portal\0 | stmt\0 |
    //          int16 num_param_formats=0 | int16 num_params=0 |
    //          int16 num_result_formats=1 | int16 fmt=1 (BINARY)
    {
        const body = [_]u8{
            0, // portal name = ""
            0, // statement name = ""
            0, 0, // 0 param-format codes
            0, 0, // 0 params
            0, 1, // 1 result-format code follows
            0, 1, // BINARY
        };
        try sendMsg(w, 'B', &body);
    }

    // Describe 'D': 'P' = describe Portal | portal_name\0
    try sendMsg(w, 'D', &[_]u8{ 'P', 0 });

    // Execute 'E': portal_name\0 | int32 max_rows=0 (unlimited)
    try sendMsg(w, 'E', &[_]u8{ 0, 0, 0, 0, 0 });

    // Sync 'S': end of pipeline; server flushes and emits ReadyForQuery.
    try w.writeAll(&[_]u8{ 'S', 0, 0, 0, 4 });
}

// =========================================================================
// Inbound protocol.
// =========================================================================

const Header = struct { tag: u8, payload_len: usize };

fn readHeader(r: *Io.Reader) !Header {
    const tag = try r.takeByte();
    const len = try r.takeInt(u32, .big);
    if (len < 4) return error.PgwireBadLength;
    return .{ .tag = tag, .payload_len = len - 4 };
}

// AuthenticationXxx: the server picks the auth mode. We handle:
//   0 = AuthenticationOk           — no password needed
//   3 = AuthenticationCleartext    — send PG_PASS as-is
//   5 = AuthenticationMD5Password  — see md5Auth below
// Anything else (SASL/SCRAM, GSS, …) errors out so the user knows to
// reconfigure QuestDB's pg.auth.* settings.
fn handleAuth(r: *Io.Reader, w: *Io.Writer) !void {
    while (true) {
        const h = try readHeader(r);
        switch (h.tag) {
            'R' => {
                if (h.payload_len < 4) return error.PgwireBadAuth;
                const sub = try r.takeInt(u32, .big);
                const remaining = h.payload_len - 4;
                switch (sub) {
                    0 => {
                        if (remaining != 0) try r.discardAll(remaining);
                        return;
                    },
                    3 => {
                        if (remaining != 0) try r.discardAll(remaining);
                        try sendPassword(w, PG_PASS);
                        try w.flush();
                    },
                    5 => {
                        if (remaining != 4) return error.PgwireBadAuth;
                        const salt = (try r.takeArray(4)).*;
                        var hashed: [35]u8 = undefined; // "md5" + 32 hex
                        md5Auth(PG_USER, PG_PASS, salt, &hashed);
                        try sendPassword(w, &hashed);
                        try w.flush();
                    },
                    else => return error.PgwireUnsupportedAuth,
                }
            },
            'E' => return errorResponse(r, h.payload_len),
            else => try r.discardAll(h.payload_len),
        }
    }
}

// Postgres MD5 auth: response = "md5" + hex(md5(hex(md5(pw + user)) + salt)).
fn md5Auth(user: []const u8, password: []const u8, salt: [4]u8, out: *[35]u8) void {
    const Md5 = std.crypto.hash.Md5;

    var d1: [16]u8 = undefined;
    var h1 = Md5.init(.{});
    h1.update(password);
    h1.update(user);
    h1.final(&d1);

    var inner_hex: [32]u8 = undefined;
    bytesToHex(&d1, &inner_hex);

    var d2: [16]u8 = undefined;
    var h2 = Md5.init(.{});
    h2.update(&inner_hex);
    h2.update(&salt);
    h2.final(&d2);

    out[0..3].* = "md5".*;
    bytesToHex(&d2, out[3..35]);
}

fn bytesToHex(in: []const u8, out: []u8) void {
    const hex = "0123456789abcdef";
    for (in, 0..) |b, i| {
        out[i * 2] = hex[b >> 4];
        out[i * 2 + 1] = hex[b & 0xf];
    }
}

// After auth, the server emits a flurry of ParameterStatus 'S', BackendKeyData
// 'K', and possibly NoticeResponse 'N' — none of which we care about. Drain
// until ReadyForQuery 'Z'.
fn waitForReady(r: *Io.Reader) !void {
    while (true) {
        const h = try readHeader(r);
        if (h.tag == 'Z') {
            try r.discardAll(h.payload_len);
            return;
        }
        if (h.tag == 'E') return errorResponse(r, h.payload_len);
        try r.discardAll(h.payload_len);
    }
}

// Drive the query loop: ParseComplete '1', BindComplete '2', RowDescription
// 'T', DataRow 'D'…, CommandComplete 'C', ReadyForQuery 'Z'. Decode each
// DataRow's binary columns into a Bar + Ts and collect them.
fn readResults(gpa: std.mem.Allocator, r: *Io.Reader, cols: Columns) !Dataset {
    var bars: std.ArrayList(Bar) = .empty;
    var timestamps: std.ArrayList(Ts) = .empty;
    errdefer bars.deinit(gpa);
    errdefer timestamps.deinit(gpa);
    try bars.ensureTotalCapacity(gpa, 2_000_000);
    try timestamps.ensureTotalCapacity(gpa, 2_000_000);

    while (true) {
        const h = try readHeader(r);
        switch (h.tag) {
            '1', '2' => try r.discardAll(h.payload_len), // ParseComplete, BindComplete
            'T' => {
                // RowDescription. We rely on column order matching our SELECT
                // (timestamp first, then OHLCV as flagged in `cols`), so the
                // contents aren't interesting — skip the whole body.
                try r.discardAll(h.payload_len);
            },
            'D' => {
                const col_count = try r.takeInt(u16, .big);
                var bar: Bar = .{};
                var ts: Ts = undefined;
                if (col_count == 0) {
                    @memset(&ts, ' ');
                    try bars.append(gpa, bar);
                    try timestamps.append(gpa, ts);
                    continue;
                }
                try readTimestampField(r, &ts);
                var i: u16 = 1;
                if (cols.open and i < col_count) {
                    bar.open = try readF8(r);
                    i += 1;
                }
                if (cols.high and i < col_count) {
                    bar.high = try readF8(r);
                    i += 1;
                }
                if (cols.low and i < col_count) {
                    bar.low = try readF8(r);
                    i += 1;
                }
                if (cols.close and i < col_count) {
                    bar.close = try readF8(r);
                    i += 1;
                }
                if (cols.volume and i < col_count) {
                    bar.volume = try readI8(r);
                    i += 1;
                }
                // Drain any unread fields (shouldn't happen but stays safe).
                while (i < col_count) : (i += 1) {
                    const flen = try r.takeInt(i32, .big);
                    if (flen > 0) try r.discardAll(@intCast(flen));
                }
                try bars.append(gpa, bar);
                try timestamps.append(gpa, ts);
            },
            'C' => try r.discardAll(h.payload_len), // CommandComplete
            'Z' => {
                try r.discardAll(h.payload_len);
                const bars_slice = try bars.toOwnedSlice(gpa);
                errdefer gpa.free(bars_slice);
                const ts_slice = try timestamps.toOwnedSlice(gpa);
                return .{
                    .bars = bars_slice,
                    .timestamps = ts_slice,
                    .allocator = gpa,
                };
            },
            'E' => {
                try r.discardAll(h.payload_len);
                return error.PgwireServerError;
            },
            else => try r.discardAll(h.payload_len), // NoticeResponse, ParameterStatus, etc.
        }
    }
}

// Read one binary float8: int32 length (must be 8) + 8 bytes IEEE 754 BE.
// Pulling the bytes as u64-BE and `@bitCast`ing to f64 skips the text parser
// entirely — this is where the speedup over CSV actually lives.
fn readF8(r: *Io.Reader) !f64 {
    const len = try r.takeInt(i32, .big);
    if (len == -1) return 0; // NULL → 0 (matches the CSV path's behavior)
    if (len != 8) return error.PgwireBadFieldLen;
    const bits = try r.takeInt(u64, .big);
    return @bitCast(bits);
}

fn readI8(r: *Io.Reader) !i64 {
    const len = try r.takeInt(i32, .big);
    if (len == -1) return 0;
    if (len != 8) return error.PgwireBadFieldLen;
    return try r.takeInt(i64, .big);
}

fn readTimestampField(r: *Io.Reader, out: *Ts) !void {
    const len = try r.takeInt(i32, .big);
    if (len == -1) {
        @memset(out, ' ');
        return;
    }
    if (len != 8) return error.PgwireBadFieldLen;
    const micros = try r.takeInt(i64, .big);
    formatTs(micros, out);
}

// Format a postgres-epoch microsecond count into "YYYY-MM-DD HH:MM" (16 ASCII
// bytes). Matches the format the old CSV parser produced so report.zig
// doesn't need to change.
fn formatTs(micros: i64, out: *Ts) void {
    const unix_secs: i64 = @divFloor(micros, 1_000_000) + PG_EPOCH_UNIX;
    // Our NQ data is post-2000, so unix_secs is always positive — the cast
    // to u64 is safe. EpochSeconds wants a u64.
    const es = std.time.epoch.EpochSeconds{ .secs = @intCast(unix_secs) };
    const ed = es.getEpochDay();
    const ymd = ed.calculateYearDay();
    const md = ymd.calculateMonthDay();
    const ds = es.getDaySeconds();

    _ = std.fmt.bufPrint(out[0..], "{d:0>4}-{d:0>2}-{d:0>2} {d:0>2}:{d:0>2}", .{
        ymd.year,
        md.month.numeric(),
        @as(u8, md.day_index) + 1,
        ds.getHoursIntoDay(),
        ds.getMinutesIntoHour(),
    }) catch unreachable;
}

// Server-side error: discard the field stream and surface as a Zig error.
// Zig errors are tag-only — we lose the human-readable message, which is the
// main downside vs. a richer error type. Add a printer here if you need it.
fn errorResponse(r: *Io.Reader, payload_len: usize) !void {
    try r.discardAll(payload_len);
    return error.PgwireServerError;
}

// Build a SQL SELECT into a caller-provided buffer. Returns the written
// slice. Plain text — pgwire transports SQL as UTF-8.
fn buildSql(buf: []u8, cols: Columns, src: Source) ![]const u8 {
    var pos: usize = 0;

    // Local helper. Zig has no closures, but you can declare an anonymous
    // struct with a function inside and pick it out via `.f`.
    const put = struct {
        fn f(b: []u8, p: *usize, s: []const u8) !void {
            if (p.* + s.len > b.len) return error.BufferTooSmall;
            @memcpy(b[p.*..][0..s.len], s);
            p.* += s.len;
        }
    }.f;

    try put(buf, &pos, "SELECT timestamp");
    if (cols.open) try put(buf, &pos, ",open");
    if (cols.high) try put(buf, &pos, ",high");
    if (cols.low) try put(buf, &pos, ",low");
    if (cols.close) try put(buf, &pos, ",close");
    if (cols.volume) try put(buf, &pos, ",volume");
    try put(buf, &pos, " FROM ");
    try put(buf, &pos, src.table);

    if (src.from) |from| {
        if (src.to) |to| {
            try put(buf, &pos, " WHERE timestamp >= '");
            try put(buf, &pos, from);
            try put(buf, &pos, "' AND timestamp < '");
            try put(buf, &pos, to);
            try put(buf, &pos, "'");
        } else {
            try put(buf, &pos, " WHERE timestamp >= '");
            try put(buf, &pos, from);
            try put(buf, &pos, "'");
        }
    } else if (src.to) |to| {
        try put(buf, &pos, " WHERE timestamp < '");
        try put(buf, &pos, to);
        try put(buf, &pos, "'");
    }

    try put(buf, &pos, " ORDER BY timestamp");
    return buf[0..pos];
}
