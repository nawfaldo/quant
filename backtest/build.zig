const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    // Default to ReleaseFast so float/int parsing runs at native speed.
    // Override with -Doptimize=Debug when you need stack traces or ASan.
    const optimize = b.standardOptimizeOption(.{ .preferred_optimize_mode = .ReleaseFast });

    const exe = b.addExecutable(.{
        .name = "backtester",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/main.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });

    exe.root_module.addIncludePath(.{ .cwd_relative = "/opt/homebrew/Cellar/sqlite/3.53.1/include" });
    exe.root_module.addLibraryPath(.{ .cwd_relative = "/opt/homebrew/Cellar/sqlite/3.53.1/lib" });
    exe.root_module.linkSystemLibrary("sqlite3", .{});
    exe.root_module.link_libc = true;

    b.installArtifact(exe);

    const run_cmd = b.addRunArtifact(exe);
    run_cmd.step.dependOn(b.getInstallStep());
    if (b.args) |args| run_cmd.addArgs(args);

    const run_step = b.step("run", "Run the app");
    run_step.dependOn(&run_cmd.step);
}
