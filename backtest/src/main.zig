const std = @import("std");
const cli = @import("cli.zig");

// Entry point. All work now happens inside the CLI — nothing is fetched or
// computed until the user explicitly asks for it with a slash-command.
pub fn main(init: std.process.Init) !void {
    try cli.run(init.io, init.gpa);
}
