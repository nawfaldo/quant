const std = @import("std");

const QUESTDB_PORT: u16 = 9000;
const RECV_BUF_BYTES: usize = 512 * 1024;

extern "c" fn socket(domain: c_uint, sock_type: c_uint, protocol: c_uint) c_int;
extern "c" fn close(fd: c_int) c_int;
extern "c" fn usleep(usec: c_uint) c_int;
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

// Streaming HTTP reader for QuestDB's /exp (CSV) endpoint.
//
// Why streaming: the nq_1m table is ~5.9M rows (~350 MB CSV). The previous
// implementation buffered the entire chunked HTTP response in memory, then
// allocated a *second* full copy to de-chunk it, then the caller parsed that
// into a binary cache — a transient spike near 1 GB per fetch. On an 8 GB
// machine that drives the whole system into swap. This reader instead de-chunks
// on the fly and hands out one CSV line at a time, so peak memory for a fetch is
// just the 512 KB recv buffer plus the (small) line-spanning accumulator. The
// binary cache being built by the caller is the only large allocation.
//
// Raw C sockets are used because std.http.Client requires a std.Io, which is
// unavailable inside zap callbacks (they run in facil.io's own event loop).
pub const Reader = struct {
    a: std.mem.Allocator,
    sock: c_int,
    rbuf: []u8,
    rstart: usize = 0,
    rend: usize = 0,
    sock_eof: bool = false,

    chunked: bool = false,
    chunk_left: usize = 0, // remaining data bytes in the current chunk
    body_done: bool = false,
    // True once the body has been fully and correctly received: the final
    // zero-size chunk (chunked) or a clean socket close (identity). If a fetch
    // ends with `complete == false` the stream was truncated and the caller
    // must discard whatever it parsed and retry.
    complete: bool = false,

    // Unconsumed bytes of the current decoded body run, pointing into `rbuf`.
    cur: []const u8 = &.{},
    // Accumulates a CSV line that spans a recv/chunk boundary. Tiny (~one row).
    line: std.ArrayList(u8) = .empty,

    pub fn deinit(self: *Reader) void {
        self.line.deinit(self.a);
        self.a.free(self.rbuf);
        _ = close(self.sock);
    }

    // Ensures rbuf holds at least one unread byte. False on socket EOF/error.
    fn fillRaw(self: *Reader) bool {
        if (self.rstart < self.rend) return true;
        if (self.sock_eof) return false;
        const n = std.c.recv(self.sock, self.rbuf.ptr, self.rbuf.len, 0);
        if (n <= 0) {
            self.sock_eof = true;
            return false;
        }
        self.rstart = 0;
        self.rend = @intCast(n);
        return true;
    }

    fn nextByte(self: *Reader) ?u8 {
        if (!self.fillRaw()) return null;
        const b = self.rbuf[self.rstart];
        self.rstart += 1;
        return b;
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

    // Returns the next contiguous run of decoded body bytes (a slice into rbuf),
    // or null at end-of-body. Sets `complete` only when the body ended cleanly.
    fn bodyChunk(self: *Reader) ?[]const u8 {
        if (self.body_done) return null;
        if (self.chunked) {
            if (self.chunk_left == 0) {
                if (!self.readChunkHeader() or self.chunk_left == 0) {
                    self.body_done = true;
                    return null;
                }
            }
            if (!self.fillRaw()) {
                self.body_done = true;
                return null;
            }
            const avail = self.rend - self.rstart;
            const take = @min(avail, self.chunk_left);
            const s = self.rbuf[self.rstart .. self.rstart + take];
            self.rstart += take;
            self.chunk_left -= take;
            return s;
        }
        if (!self.fillRaw()) {
            self.body_done = true;
            self.complete = true; // identity body ends at clean socket close
            return null;
        }
        const s = self.rbuf[self.rstart..self.rend];
        self.rstart = self.rend;
        return s;
    }

    // Returns the next CSV line (without trailing CRLF), or null at end-of-body.
    // The returned slice is valid only until the next nextLine() call. Memory
    // errors on the spanning accumulator surface as a truncated stream (null
    // with complete == false), which the retry path handles.
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

fn stripCr(s: []const u8) []const u8 {
    return if (s.len > 0 and s[s.len - 1] == '\r') s[0 .. s.len - 1] else s;
}

fn parseIp4(s: []const u8) ![4]u8 {
    var it = std.mem.splitScalar(u8, s, '.');
    const a0 = try std.fmt.parseInt(u8, it.next() orelse return error.Invalid, 10);
    const a1 = try std.fmt.parseInt(u8, it.next() orelse return error.Invalid, 10);
    const a2 = try std.fmt.parseInt(u8, it.next() orelse return error.Invalid, 10);
    const a3 = try std.fmt.parseInt(u8, it.next() orelse return error.Invalid, 10);
    return .{ a0, a1, a2, a3 };
}

// Opens a connection to QuestDB, sends the /exp query, and consumes the HTTP
// response headers so the returned Reader is positioned at the first body byte.
// CSV (/exp) is used over JSON (/exec) because it serializes ~2.6x faster for
// large result sets. Caller owns the Reader and must deinit() it.
pub fn open(a: std.mem.Allocator, sql: []const u8) !Reader {
    const encoded = try urlEncode(a, sql);
    defer a.free(encoded);

    // QUESTDB_HOST lets WSL builds reach a QuestDB running on the Windows host.
    // safe-build.sh sets it to the WSL gateway IP automatically.
    const host_cstr = getenv("QUESTDB_HOST");
    const host_str = if (host_cstr) |p| std.mem.sliceTo(p, 0) else "127.0.0.1";
    const ip4 = parseIp4(host_str) catch [4]u8{ 127, 0, 0, 1 };

    const req_str = try std.fmt.allocPrint(a,
        "GET /exp?query={s} HTTP/1.1\r\nHost: {s}:9000\r\nConnection: close\r\n\r\n",
        .{ encoded, host_str },
    );
    defer a.free(req_str);

    const sock = socket(@intCast(std.c.AF.INET), std.c.SOCK.STREAM, 0);
    if (sock < 0) return error.SocketFailed;
    errdefer _ = close(sock);

    // Bump SO_RCVBUF: the small macOS default stalls the localhost sender.
    const rcvbuf: c_int = 8 * 1024 * 1024;
    _ = std.c.setsockopt(sock, std.c.SOL.SOCKET, std.c.SO.RCVBUF, &rcvbuf, @sizeOf(c_int));
    // Disable Nagle/delayed-ACK coalescing, which throttles a chunked sender on
    // localhost to ~10 MB/s.
    const one: c_int = 1;
    _ = std.c.setsockopt(sock, std.c.IPPROTO.TCP, std.c.TCP.NODELAY, &one, @sizeOf(c_int));
    // Safety net: QuestDB ignores "Connection: close" and holds the socket open
    // after the final chunk, so a missed end-of-stream would otherwise block
    // forever. 30 s is far longer than any healthy inter-packet gap.
    const rcvtimeo = std.c.timeval{ .sec = 30, .usec = 0 };
    _ = std.c.setsockopt(sock, std.c.SOL.SOCKET, std.c.SO.RCVTIMEO, &rcvtimeo, @sizeOf(std.c.timeval));

    const addr = std.c.sockaddr.in{
        .port = std.mem.nativeToBig(u16, QUESTDB_PORT),
        .addr = @bitCast(ip4),
    };
    if (std.c.connect(sock, @ptrCast(&addr), @sizeOf(std.c.sockaddr.in)) != 0)
        return error.ConnectFailed;

    var sent: usize = 0;
    while (sent < req_str.len) {
        const n = std.c.send(sock, req_str[sent..].ptr, req_str.len - sent, 0);
        if (n < 0) return error.SendFailed;
        sent += @intCast(n);
    }

    const rbuf = try a.alloc(u8, RECV_BUF_BYTES);
    errdefer a.free(rbuf);

    var rd = Reader{ .a = a, .sock = sock, .rbuf = rbuf };

    // Consume HTTP headers (terminated by CRLFCRLF) and detect chunked encoding.
    // Headers are small; scanning byte-by-byte is cheap.
    var hdr: [16 * 1024]u8 = undefined;
    var hlen: usize = 0;
    while (true) {
        const b = rd.nextByte() orelse return error.BadResponse;
        if (hlen < hdr.len) {
            hdr[hlen] = b;
            hlen += 1;
        } else return error.HeaderTooLarge;
        if (hlen >= 4 and std.mem.eql(u8, hdr[hlen - 4 .. hlen], "\r\n\r\n")) break;
    }
    rd.chunked = std.mem.indexOf(u8, hdr[0..hlen], "chunked") != null;
    return rd;
}
