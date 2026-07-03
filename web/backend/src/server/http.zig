const std = @import("std");

// Minimal cross-platform HTTP/1.1 server built on std.Io.net (works natively on
// Windows, macOS, and Linux — no zap/facil.io, which is POSIX-only). `Ctx`
// mirrors the small slice of zap's Request surface that router.zig relies on, so
// the routing code is unchanged apart from the request type.

pub const ContentType = enum { JSON };

pub const Ctx = struct {
    io: std.Io,
    alloc: std.mem.Allocator,

    method: ?[]const u8,
    path: ?[]const u8,
    query: ?[]const u8,
    body: ?[]const u8,

    // Response state, flushed by writeResponse() after the handler returns.
    status: u16 = 200,
    content_type: []const u8 = "application/json",
    resp_body: []const u8 = "",
    owns_body: bool = false,

    pub fn setStatusNumeric(self: *Ctx, code: u16) void {
        self.status = code;
    }

    pub fn setContentType(self: *Ctx, ct: ContentType) !void {
        switch (ct) {
            .JSON => self.content_type = "application/json",
        }
    }

    // Only Content-Type is honored here; CORS headers are emitted unconditionally
    // by writeResponse(), which is the only other header router.zig ever sets.
    pub fn setHeader(self: *Ctx, name: []const u8, value: []const u8) !void {
        if (std.ascii.eqlIgnoreCase(name, "Content-Type")) {
            self.content_type = value;
        }
    }

    // The handler's body slice may point into a buffer that is freed (router uses
    // `defer alloc.free(body)` right after sending), so copy it into Ctx-owned
    // memory that survives until the response is written.
    pub fn sendBody(self: *Ctx, body: []const u8) !void {
        self.resp_body = try self.alloc.dupe(u8, body);
        self.owns_body = true;
    }

    pub fn sendJson(self: *Ctx, body: []const u8) !void {
        self.content_type = "application/json";
        return self.sendBody(body);
    }
};

fn statusText(code: u16) []const u8 {
    return switch (code) {
        200 => "200 OK",
        400 => "400 Bad Request",
        404 => "404 Not Found",
        405 => "405 Method Not Allowed",
        500 => "500 Internal Server Error",
        503 => "503 Service Unavailable",
        else => "200 OK",
    };
}

// Parses one HTTP request off `conn`, dispatches to `handler`, and writes the
// response. `handler` is `fn (*Ctx) anyerror!void` (router.onRequest).
pub fn handleConnection(
    io: std.Io,
    conn: std.Io.net.Stream,
    read_buf: []u8,
    write_buf: []u8,
    alloc: std.mem.Allocator,
    comptime handler: anytype,
) !void {
    defer conn.close(io);

    var reader = conn.reader(io, read_buf);
    const r = &reader.interface;

    // Request line: METHOD TARGET HTTP/1.1
    const req_line_opt = try r.takeDelimiter('\n');
    const req_line = req_line_opt orelse return;
    const line_trim = std.mem.trimEnd(u8, req_line, "\r");
    var parts = std.mem.tokenizeScalar(u8, line_trim, ' ');
    const method_raw = parts.next() orelse "";
    const target_raw = parts.next() orelse "/";

    // Copy method + target into stable buffers — the reader's buffer gets reused
    // when we read the body, which would otherwise dangle these slices.
    var method_buf: [16]u8 = undefined;
    const ml = @min(method_raw.len, method_buf.len);
    @memcpy(method_buf[0..ml], method_raw[0..ml]);

    var target_buf: [2048]u8 = undefined;
    const tl = @min(target_raw.len, target_buf.len);
    @memcpy(target_buf[0..tl], target_raw[0..tl]);
    const target = target_buf[0..tl];

    var path_slice: []const u8 = target;
    var query_slice: ?[]const u8 = null;
    if (std.mem.indexOfScalar(u8, target, '?')) |qi| {
        path_slice = target[0..qi];
        query_slice = target[qi + 1 ..];
    }

    // Headers — we only care about Content-Length.
    var content_length: usize = 0;
    while (true) {
        const hdr_opt = try r.takeDelimiter('\n');
        const hdr_raw = hdr_opt orelse break;
        const hdr = std.mem.trimEnd(u8, hdr_raw, "\r");
        if (hdr.len == 0) break;
        if (std.ascii.startsWithIgnoreCase(hdr, "content-length:")) {
            const v = std.mem.trim(u8, hdr["content-length:".len..], " ");
            content_length = std.fmt.parseInt(usize, v, 10) catch 0;
        }
    }

    // Body (POST). `take` returns a slice into read_buf, valid for the duration
    // of the handler call (we do not read from `r` again afterward).
    var body: ?[]const u8 = null;
    if (content_length > 0) {
        body = r.take(content_length) catch null;
    }

    var ctx = Ctx{
        .io = io,
        .alloc = alloc,
        .method = method_buf[0..ml],
        .path = path_slice,
        .query = query_slice,
        .body = body,
    };
    defer if (ctx.owns_body) alloc.free(ctx.resp_body);

    handler(&ctx) catch |err| {
        std.debug.print("handler error: {any}\n", .{err});
        if (!ctx.owns_body) {
            ctx.status = 500;
            ctx.sendJson("{\"error\":\"internal\"}") catch {};
        }
    };

    try writeResponse(io, conn, write_buf, &ctx);
}

fn writeResponse(io: std.Io, conn: std.Io.net.Stream, write_buf: []u8, ctx: *const Ctx) !void {
    var writer = conn.writer(io, write_buf);
    const w = &writer.interface;
    try w.print("HTTP/1.1 {s}\r\n", .{statusText(ctx.status)});
    try w.print("Content-Type: {s}\r\n", .{ctx.content_type});
    try w.print("Content-Length: {d}\r\n", .{ctx.resp_body.len});
    try w.print("Access-Control-Allow-Origin: *\r\n", .{});
    try w.print("Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS\r\n", .{});
    try w.print("Access-Control-Allow-Headers: Content-Type\r\n", .{});
    try w.print("Connection: close\r\n\r\n", .{});
    if (ctx.resp_body.len > 0) try w.writeAll(ctx.resp_body);
    try w.flush();
}
