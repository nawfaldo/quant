const std = @import("std");
const data = @import("../bt/data.zig");

// ── Hardcoded trade filters ──────────────────────────────────────────────────
//
// Shared day-of-week / hour-of-day entry filters. The lists are EXCLUSIONS:
// a listed day/hour is skipped, everything else trades. Each strategy declares
// its own hardcoded lists and calls `allowed` before taking a NEW entry (exits
// and EOD flattens are never filtered):
//
//   const filter_day: []const []const u8 = &.{ "Monday", "Tuesday" };  // no Mon/Tue entries
//   const filter_hour: []const []const u8 = &.{ "09.00", "12.00" };    // no 09:xx/12:xx entries
//
// An empty list means that filter is off (everything passes). Hour entries use
// "HH.MM" format but only the hour is compared — "12.00" blocks any bar whose
// timestamp falls in the 12:xx hour.

const day_names = [7][]const u8{
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
};

pub fn allowed(days: []const []const u8, hours: []const []const u8, ts: data.Ts) bool {
    return dayAllowed(days, ts) and hourAllowed(hours, ts);
}

pub fn dayAllowed(days: []const []const u8, ts: data.Ts) bool {
    if (days.len == 0) return true;
    const name = weekdayName(ts) orelse return true;
    for (days) |d| {
        if (std.ascii.eqlIgnoreCase(d, name)) return false;
    }
    return true;
}

pub fn hourAllowed(hours: []const []const u8, ts: data.Ts) bool {
    for (hours) |h| {
        if (h.len >= 2 and std.mem.eql(u8, h[0..2], ts[11..13])) return false;
    }
    return true;
}

fn weekdayName(ts: data.Ts) ?[]const u8 {
    const y = std.fmt.parseInt(i64, ts[0..4], 10) catch return null;
    const m = std.fmt.parseInt(i64, ts[5..7], 10) catch return null;
    const d = std.fmt.parseInt(i64, ts[8..10], 10) catch return null;
    // Days since 1970-01-01 (Howard Hinnant's days-from-civil), which was a
    // Thursday → +4 lands Sunday on index 0.
    const yy = if (m <= 2) y - 1 else y;
    const era = @divFloor(yy, 400);
    const yoe = yy - era * 400;
    const doy = @divFloor(153 * @mod(m + 9, 12) + 2, 5) + d - 1;
    const doe = yoe * 365 + @divFloor(yoe, 4) - @divFloor(yoe, 100) + doy;
    const epoch_days = era * 146097 + doe - 719468;
    const idx: usize = @intCast(@mod(epoch_days + 4, 7));
    return day_names[idx];
}
