# Quant / web

Zig backtest + live-trading backend (`backend/`), React frontend (`frontend/`), offline
TensorFlow training (`python/`). Each has its own AGENT.md.

## The machine: 8 GB M2 MacBook Pro — read this before running anything

| | |
|---|---|
| Model | MacBook Pro `Mac14,7` |
| Chip | Apple M2 — 8 cores (**4 performance**, 4 efficiency) |
| Memory | **8 GB, unified** |

Every heavy tool in this repo defaults to sizing itself off the *core count* (8) while the
real constraint is *memory* (8 GB, shared with the GPU and the OS). That mismatch has
already taken this machine down. When RAM runs out macOS swaps to disk, the UI stops
responding, and it reads as a hang or a crash rather than as memory pressure.

**The four rules below are not suggestions. Follow them without being asked.**

### 1. Never dump unbounded output to the terminal

This kills the *terminal*, not the machine, and it is the cheapest mistake to make. The
terminal buffers and renders every byte it receives.

- Never `cat` a binary artifact — `python/artifacts/deep_momentum/*/signals.bin`, any
  `/api/*/bin` response, `*.db`.
- Never print a full QuestDB result set. Add `LIMIT`, or pipe through `head`.
- Pipe long logs to a file and `grep` it; don't stream them to stdout.
- When in doubt, append `| head -50`.

### 2. Never run Deep Momentum training locally

`deep-momentum train` spawns `DM_TRIAL_WORKERS` TensorFlow processes. It runs on the PC
over SSH (`JawirGaming66@100.81.28.51`), never here — including the smoke run.
See `python/AGENT.md`.

### 3. Cap anything that sizes itself off the core count

- **`zig build`** compiles the bundled SQLite amalgamation (`src/vendor/sqlite3.c`, a
  single very large C file) and parallelises across all cores by default. Use
  `zig build -j2`.
- **The tuner** (`backend/src/bt/tune.zig`) calls `std.Thread.getCpuCount()` and spawns
  that many workers with **no upper bound**, each holding its own working set over the
  dataset. Cap it before starting a large sweep. Prefer a small combo grid on this
  machine; large sweeps belong on the PC.

### 4. Never run the whole stack at once

QuestDB is a JVM process and reserves a large heap simply by being up. The budget does not
survive all of these together:

```
QuestDB  +  zig build run (:8080)  +  frontend dev server  +  a build / tune / train
```

Start what you need, stop it when you're done. Before a build or a sweep, shut down
QuestDB and the dev server. Check first — `ps aux | grep -i questdb`.

## Precedent

This is not theoretical. `backend/AGENT.md` records that eager QuestDB prefetch at
startup (pre-building all 7 timeframes + VWAP) "thrashed an 8 GB Mac", which is why
`cache.zig` now builds each blob on demand and caches nothing. Peak memory is one blob at
a time, deliberately. Preserve that property.
