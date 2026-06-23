const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{ .preferred_optimize_mode = .ReleaseFast });

    // ── SQLite amalgamation (C source, bundled) ───────────────────────────────
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

    // ── signal_runner: Python IPC bridge (stdin/stdout protocol) ─────────────
    const signal_runner = b.addExecutable(.{
        .name = "signal_runner",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/main.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    b.installArtifact(signal_runner);

    // ── api: HTTP API server ───────────────────────────────────────────────
    const api_mod = b.createModule(.{
        .root_source_file = b.path("src/api.zig"),
        .target = target,
        .optimize = optimize,
    });
    api_mod.addIncludePath(b.path("src")); // for sqlite3.h

    const api = b.addExecutable(.{
        .name = "api",
        .root_module = api_mod,
    });
    api.root_module.linkLibrary(sqlite);
    api.root_module.link_libc = true;
    api.root_module.linkSystemLibrary("winhttp", .{});
    b.installArtifact(api);

    const run_cmd = b.addRunArtifact(api);
    run_cmd.step.dependOn(b.getInstallStep());
    if (b.args) |args| run_cmd.addArgs(args);

    const run_step = b.step("run", "Start the march API server (port 4000)");
    run_step.dependOn(&run_cmd.step);
}
