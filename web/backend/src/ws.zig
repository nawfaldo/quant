// ws.zig — Minimal WebSocket client over raw Winsock2.
//
// Used by api.zig to subscribe to the Bookmap live-push server (port 8765)
// and receive real-time trade ticks for strategy processing.

const std = @import("std");

// ── Winsock2 extern declarations ──────────────────────────────────────────────

const SOCKET = usize;
const INVALID_SOCKET = ~@as(SOCKET, 0);

const SockAddrIn = extern struct {
    sin_family: i16 = 2, // AF_INET
    sin_port: u16 = 0, // big-endian
    sin_addr: u32 = 0, // big-endian
    sin_zero: [8]u8 = [_]u8{0} ** 8,
};

extern "ws2_32" fn WSAStartup(wVersionRequested: u16, lpWSAData: [*]u8) callconv(.winapi) i32;
extern "ws2_32" fn WSACleanup() callconv(.winapi) i32;
extern "ws2_32" fn socket(af: i32, sock_type: i32, protocol: i32) callconv(.winapi) SOCKET;
extern "ws2_32" fn connect(s: SOCKET, name: *const SockAddrIn, namelen: i32) callconv(.winapi) i32;
extern "ws2_32" fn send(s: SOCKET, buf: [*]const u8, len: i32, flags: i32) callconv(.winapi) i32;
extern "ws2_32" fn recv(s: SOCKET, buf: [*]u8, len: i32, flags: i32) callconv(.winapi) i32;
extern "ws2_32" fn closesocket(s: SOCKET) callconv(.winapi) i32;

const AF_INET: i32 = 2;
const SOCK_STREAM: i32 = 1;

// ── Public types ──────────────────────────────────────────────────────────────

pub const WsError = error{
    WsaStartup,
    ConnectFailed,
    UpgradeFailed,
    SendFailed,
    RecvFailed,
    FrameTooLarge,
    ConnectionClosed,
};

pub const FrameType = enum { text, binary, close, ping, pong, continuation };

pub const Frame = struct {
    frame_type: FrameType,
    payload: []const u8,
    fin: bool,
};

// ── Init / cleanup ────────────────────────────────────────────────────────────

pub fn initWsa() WsError!void {
    var wsa_data: [512]u8 = undefined;
    if (WSAStartup(0x0202, &wsa_data) != 0) return WsError.WsaStartup;
}

pub fn cleanupWsa() void {
    _ = WSACleanup();
}

// ── WsClient ──────────────────────────────────────────────────────────────────

pub const WsClient = struct {
    sock: SOCKET = INVALID_SOCKET,

    /// TCP-connect to the given IPv4 address and port.
    pub fn connectTo(host_ip: u32, port: u16) WsError!WsClient {
        const s = socket(AF_INET, SOCK_STREAM, 0);
        if (s == INVALID_SOCKET) return WsError.ConnectFailed;

        const addr = SockAddrIn{
            .sin_port = @byteSwap(port),
            .sin_addr = @byteSwap(host_ip),
        };

        if (connect(s, &addr, @sizeOf(SockAddrIn)) != 0) {
            _ = closesocket(s);
            return WsError.ConnectFailed;
        }

        return WsClient{ .sock = s };
    }

    /// Send the HTTP upgrade request and validate the 101 response.
    pub fn upgrade(self: *WsClient) WsError!void {
        const req =
            "GET / HTTP/1.1\r\n" ++
            "Host: 127.0.0.1:8765\r\n" ++
            "Upgrade: websocket\r\n" ++
            "Connection: Upgrade\r\n" ++
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" ++
            "Sec-WebSocket-Version: 13\r\n" ++
            "\r\n";

        try self.sendAll(req);

        // Read until the end of the HTTP response (\r\n\r\n).
        var resp_buf: [2048]u8 = undefined;
        var resp_len: usize = 0;
        while (resp_len < resp_buf.len) {
            const n = recv(self.sock, resp_buf[resp_len..].ptr, @intCast(resp_buf.len - resp_len), 0);
            if (n <= 0) return WsError.UpgradeFailed;
            resp_len += @intCast(n);
            if (resp_len >= 4) {
                if (std.mem.indexOf(u8, resp_buf[0..resp_len], "\r\n\r\n")) |_| break;
            }
        }

        if (!std.mem.startsWith(u8, resp_buf[0..resp_len], "HTTP/1.1 101")) {
            return WsError.UpgradeFailed;
        }
    }

    /// Read one WebSocket frame. The payload is written into `buf`.
    pub fn readFrame(self: *WsClient, buf: []u8) WsError!Frame {
        // 2-byte header.
        var header: [2]u8 = undefined;
        try self.recvExact(&header);

        const fin = (header[0] & 0x80) != 0;
        const opcode: u4 = @truncate(header[0] & 0x0F);
        const masked = (header[1] & 0x80) != 0;
        var payload_len: u64 = header[1] & 0x7F;

        // Extended payload length.
        if (payload_len == 126) {
            var ext: [2]u8 = undefined;
            try self.recvExact(&ext);
            payload_len = @as(u64, ext[0]) << 8 | @as(u64, ext[1]);
        } else if (payload_len == 127) {
            var ext: [8]u8 = undefined;
            try self.recvExact(&ext);
            payload_len = 0;
            for (ext) |b| payload_len = (payload_len << 8) | b;
        }

        if (payload_len > buf.len) return WsError.FrameTooLarge;
        const plen: usize = @intCast(payload_len);

        // Masking key (server→client frames should NOT be masked, but handle it).
        var mask_key: [4]u8 = undefined;
        if (masked) try self.recvExact(&mask_key);

        // Payload.
        if (plen > 0) {
            try self.recvExact(buf[0..plen]);
            if (masked) {
                for (0..plen) |i| buf[i] ^= mask_key[i % 4];
            }
        }

        const frame_type: FrameType = switch (opcode) {
            0x0 => .continuation,
            0x1 => .text,
            0x2 => .binary,
            0x8 => .close,
            0x9 => .ping,
            0xA => .pong,
            else => .text,
        };

        return Frame{ .frame_type = frame_type, .payload = buf[0..plen], .fin = fin };
    }

    /// Send a pong reply (client→server frames MUST be masked per RFC 6455).
    pub fn sendPong(self: *WsClient, payload: []const u8) WsError!void {
        if (payload.len > 125) return;
        var frame: [131]u8 = undefined; // 2 header + 4 mask + 125 max payload
        frame[0] = 0x8A; // FIN + pong opcode
        frame[1] = 0x80 | @as(u8, @intCast(payload.len)); // MASK bit + length
        // Fixed mask key (value doesn't matter for pong).
        frame[2] = 0x37;
        frame[3] = 0xFA;
        frame[4] = 0x21;
        frame[5] = 0x3D;
        for (payload, 0..) |b, i| {
            frame[6 + i] = b ^ frame[2 + (i % 4)];
        }
        try self.sendAll(frame[0 .. 6 + payload.len]);
    }

    pub fn close(self: *WsClient) void {
        if (self.sock != INVALID_SOCKET) {
            _ = closesocket(self.sock);
            self.sock = INVALID_SOCKET;
        }
    }

    // ── Internal helpers ──────────────────────────────────────────────────

    fn sendAll(self: *WsClient, bytes: []const u8) WsError!void {
        var sent: usize = 0;
        while (sent < bytes.len) {
            const n = send(self.sock, bytes[sent..].ptr, @intCast(bytes.len - sent), 0);
            if (n <= 0) return WsError.SendFailed;
            sent += @intCast(n);
        }
    }

    fn recvExact(self: *WsClient, buf: []u8) WsError!void {
        var got: usize = 0;
        while (got < buf.len) {
            const n = recv(self.sock, buf[got..].ptr, @intCast(buf.len - got), 0);
            if (n < 0) return WsError.RecvFailed;
            if (n == 0) return WsError.ConnectionClosed;
            got += @intCast(n);
        }
    }
};
