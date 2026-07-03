const std = @import("std");

const QUESTDB_PORT: u16 = 9000;
const STREAM_BUF_BYTES: usize = 512 * 1024;

extern "c" fn getenv(name: [*:0]const u8) ?[*:0]const u8;

fn urlEncode(a: std.mem.Allocator, input: []const u8) ![]u8 {
    var out: std.ArrayList(u8) = .empty;
    for (input) |c| {
        switch (c) {
            'A'...'Z', 'a'...'z', '0'...'9', '-', '_', '.', '~' => try out.append(a, c),
            else => {
                var hex: [3]u8 = undefined;
                const s = try std.fmt.bufPrint(&hex, "%{X:0>2}", .{c});
                try out.appendSlice(a, s);
            },
        }
    }
    return out.toOwnedSlice(a);
}

fn hexVal(c: u8) ?usize {
    return switch (c) {
        '0'...'9' => c - '0',
        'a'...'f' => c - 'a' + 10,
        'A'...'F' => c - 'A' + 10,
        else => null,
    };
}

fn stripCr(s: []const u8) []const u8 {
    return if (s.len > 0 and s[s.len - 1] == '\r') s[0 .. s.len - 1] else s;
}

// Streaming HTTP reader for QuestDB's /exp (CSV) endpoint.
//
// Why streaming: the nq_1m table is ~5.9M rows (~350 MB CSV). De-chunking on the
// fly and handing out one CSV line at a time keeps peak memory to the recv
// buffer plus a tiny line-spanning accumulator, instead of buffering the whole
// response twice.
//
// Transport is std.Io.net (cross-platform: Windows / macOS / Linux). The Reader
// is heap-allocated so its embedded std.Io reader interface has a stable address
// (the interface resolves its parent via @fieldParentPtr, which breaks if the
// struct is copied to a new location after construction).
pub const Reader = struct {
    a: std.mem.Allocator,
    io: std.Io,
    stream: std.Io.net.Stream,
    sr: std.Io.net.Stream.Reader,
    srbuf: []u8,
    eof: bool = false,

    chunked: bool = false,
    chunk_left: usize = 0, // remaining data bytes in the current chunk
    body_done: bool = false,
    // True once the body has been fully and correctly received: the final
    // zero-size chunk (chunked) or a clean socket close (identity). If a fetch
    // ends with `complete == false` the stream was truncated and the caller must
    // discard whatever it parsed and retry.
    complete: bool = false,

    // Unconsumed bytes of the current decoded body run, pointing into the reader
    // buffer (valid only until the next bodyChunk()).
    cur: []const u8 = &.{},
    // Accumulates a CSV line that spans a recv/chunk boundary. Tiny (~one row).
    line: std.ArrayList(u8) = .empty,

    fn r(self: *Reader) *std.Io.Reader {
        return &self.sr.interface;
    }

    pub fn deinit(self: *Reader) void {
        self.line.deinit(self.a);
        self.stream.close(self.io);
        const a = self.a;
        a.free(self.srbuf);
        a.destroy(self);
    }

    // Ensures the reader holds at least one buffered byte. False on EOF/error.
    fn ensure(self: *Reader) bool {
        if (self.eof) return false;
        if (self.r().bufferedLen() > 0) return true;
        self.r().fillMore() catch {
            self.eof = true;
            return false;
        };
        return self.r().bufferedLen() > 0;
    }

    fn nextByte(self: *Reader) ?u8 {
        return self.r().takeByte() catch null;
    }

    // Reads the next chunk-size line, setting chunk_left. Tolerates the trailing
    // CRLF of the previous chunk (skipped as leading whitespace) and chunk
    // extensions (skipped up to the line's '\n'). Returns false on truncation.
    fn readChunkHeader(self: *Reader) bool {
        var b = self.nextByte() orelse return false;
        while (b == '\r' or b == '\n') b = self.nextByte() orelse return false;
        var size: usize = 0;
        while (hexVal(b)) |d| {
            size = size * 16 + d;
            b = self.nextByte() orelse return false;
        }
        while (b != '\n') b = self.nextByte() orelse return false;
        self.chunk_left = size;
        if (size == 0) self.complete = true;
        return true;
    }

    // Returns the next contiguous run of decoded body bytes (a slice into the
    // reader buffer), or null at end-of-body. Sets `complete` only on a clean end.
    fn bodyChunk(self: *Reader) ?[]const u8 {
        if (self.body_done) return null;
        if (self.chunked) {
            if (self.chunk_left == 0) {
                if (!self.readChunkHeader() or self.chunk_left == 0) {
                    self.body_done = true;
                    return null;
                }
            }
            if (!self.ensure()) {
                self.body_done = true;
                return null;
            }
            const buf = self.r().buffered();
            const take = @min(buf.len, self.chunk_left);
            const s = buf[0..take];
            self.r().toss(take);
            self.chunk_left -= take;
            return s;
        }
        if (!self.ensure()) {
            self.body_done = true;
            self.complete = true; // identity body ends at clean socket close
            return null;
        }
        const buf = self.r().buffered();
        self.r().toss(buf.len);
        return buf;
    }

    // Returns the next CSV line (without trailing CRLF), or null at end-of-body.
    // The returned slice is valid only until the next nextLine() call.
    pub fn nextLine(self: *Reader) ?[]const u8 {
        self.line.clearRetainingCapacity();
        var used_accum = false;
        while (true) {
            if (self.cur.len == 0) {
                self.cur = self.bodyChunk() orelse {
                    if (used_accum and self.line.items.len > 0) return stripCr(self.line.items);
                    return null;
                };
                if (self.cur.len == 0) continue;
            }
            if (std.mem.indexOfScalar(u8, self.cur, '\n')) |nl| {
                const seg = self.cur[0..nl];
                self.cur = self.cur[nl + 1 ..];
                if (!used_accum) return stripCr(seg);
                self.line.appendSlice(self.a, seg) catch {
                    self.complete = false;
                    return null;
                };
                return stripCr(self.line.items);
            }
            self.line.appendSlice(self.a, self.cur) catch {
                self.complete = false;
                return null;
            };
            self.cur = &.{};
            used_accum = true;
        }
    }
};

// Opens a connection to QuestDB, sends the /exp query, and consumes the HTTP
// response headers so the returned Reader is positioned at the first body byte.
// CSV (/exp) is used over JSON (/exec) because it serializes ~2.6x faster for
// large result sets. Caller owns the returned Reader and must deinit() it.
pub fn open(io: std.Io, a: std.mem.Allocator, sql: []const u8) !*Reader {
    const encoded = try urlEncode(a, sql);
    defer a.free(encoded);

    // QUESTDB_HOST allows pointing at a non-local QuestDB; defaults to localhost.
    const host_cstr = getenv("QUESTDB_HOST");
    const host_str = if (host_cstr) |p| std.mem.sliceTo(p, 0) else "127.0.0.1";

    const req_str = try std.fmt.allocPrint(
        a,
        "GET /exp?query={s} HTTP/1.1\r\nHost: {s}:9000\r\nConnection: close\r\n\r\n",
        .{ encoded, host_str },
    );
    defer a.free(req_str);

    const addr = std.Io.net.IpAddress.parse(host_str, QUESTDB_PORT) catch
        try std.Io.net.IpAddress.parse("127.0.0.1", QUESTDB_PORT);

    var stream = try addr.connect(io, .{ .mode = .stream });
    errdefer stream.close(io);

    // Send the request.
    {
        var wbuf: [1024]u8 = undefined;
        var sw = stream.writer(io, &wbuf);
        const w = &sw.interface;
        try w.writeAll(req_str);
        try w.flush();
    }

    const srbuf = try a.alloc(u8, STREAM_BUF_BYTES);
    errdefer a.free(srbuf);

    const self = try a.create(Reader);
    errdefer a.destroy(self);

    self.* = .{
        .a = a,
        .io = io,
        .stream = stream,
        .sr = stream.reader(io, srbuf),
        .srbuf = srbuf,
    };

    // Consume HTTP headers (terminated by CRLFCRLF) and detect chunked encoding.
    var hdr: [16 * 1024]u8 = undefined;
    var hlen: usize = 0;
    while (true) {
        const b = self.nextByte() orelse return error.BadResponse;
        if (hlen < hdr.len) {
            hdr[hlen] = b;
            hlen += 1;
        } else return error.HeaderTooLarge;
        if (hlen >= 4 and std.mem.eql(u8, hdr[hlen - 4 .. hlen], "\r\n\r\n")) break;
    }
    self.chunked = std.mem.indexOf(u8, hdr[0..hlen], "chunked") != null;
    return self;
}
