const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{ .preferred_optimize_mode = .ReleaseFast });

    // ── SQLite amalgamation (C source, bundled) ───────────────────────────────
    // Bundled (not system-linked) so the backend builds the same on Windows,
    // macOS, and Linux without a system sqlite3 dependency.
    const sqlite_mod = b.createModule(.{
        .target = target,
        .optimize = optimize,
    });
    const sqlite = b.addLibrary(.{
        .name = "sqlite3",
        .root_module = sqlite_mod,
        .linkage = .static,
    });
    sqlite.root_module.addCSourceFile(.{
        .file = b.path("src/sqlite3.c"),
        .flags = &.{
            "-DSQLITE_THREADSAFE=1",
            "-DSQLITE_DEFAULT_WAL_SYNCHRONOUS=1",
        },
    });
    sqlite.root_module.link_libc = true;

    const root_mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    root_mod.addIncludePath(b.path("src")); // for sqlite3.h
    root_mod.linkLibrary(sqlite);

    if (target.result.os.tag == .windows) {
        // std.Io.net's Windows backend needs Winsock; march ws.zig also uses ws2_32.
        root_mod.linkSystemLibrary("ws2_32", .{});
    }

    const exe = b.addExecutable(.{
        .name = "backend",
        .root_module = root_mod,
    });

    b.installArtifact(exe);

    // signal_runner: the stdin/stdout strategy bridge the Python side spawns
    // (used by march/python tests). Cross-platform; no sqlite / winhttp.
    const sr_mod = b.createModule(.{
        .root_source_file = b.path("src/signal_runner.zig"),
        .target = target,
        .optimize = optimize,
    });
    const signal_runner = b.addExecutable(.{
        .name = "signal_runner",
        .root_module = sr_mod,
    });
    b.installArtifact(signal_runner);

    const run_cmd = b.addRunArtifact(exe);
    run_cmd.step.dependOn(b.getInstallStep());
    if (b.args) |args| run_cmd.addArgs(args);

    const run_step = b.step("run", "Run the backend (port 8080)");
    run_step.dependOn(&run_cmd.step);
}
