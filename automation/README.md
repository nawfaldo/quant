# Tune Automation Runner

An external, **read-only** automation client for the quant backend's tuning API.
It runs many hyperparameter experiments sequentially, shows live progress, and
saves every result artifact — without touching backend code. It only speaks HTTP
to the endpoints the backend already exposes:

- `POST /api/tune`
- `GET  /api/tune/status`
- `GET  /api/tune/results.csv`
- `GET  /api/tune/results.json`
- `GET  /api/tune/report.md`
- `GET  /api/tune/heatmap.json`

## Requirements

- Python **3.12+**
- The backend running (default `http://localhost:8080`) with QuestDB up and data
  loaded. From the repo root: `run-questdb.bat` then `run-backend.bat` (or
  `run-all.bat`).

## Installation

```bash
cd automation
python -m venv .venv
```

Activate the venv, then install. **Run each line separately** — Windows
PowerShell 5.1 does not support `&&`:

```powershell
# Windows PowerShell
.venv\Scripts\activate
pip install -r requirements.txt
```

```bash
# macOS / Linux (bash/zsh)
source .venv/bin/activate
pip install -r requirements.txt
```

> Tip: without activating, you can always call the venv's interpreter directly:
> `.venv\Scripts\python.exe runner.py` (Windows) or `.venv/bin/python runner.py`.

## Configuration

Edit `config.yaml`. Each entry under `experiments` is one grid search. List-valued
params are **comma-separated strings** (the backend sweeps their cartesian
product); single values are one-element lists.

```yaml
base_url: "http://localhost:8080"   # backend URL (CLI --base-url overrides)
output_dir: "results"               # where artifacts are written

poll:                               # status-poll cadence (no overall timeout)
  initial_seconds: 1.0
  max_seconds: 10.0
  backoff: 1.5                      # exponential backoff multiplier

http:
  retries: 3                        # retry transient HTTP failures
  retry_backoff_seconds: 2.0

experiments:
  - name: RTH_VWAP
    strategy: "RTH VWAP"            # exactly: "RTH VWAP" | "30m Buy" | "5m ORB"
    symbol: NQ                      # NQ | GBPUSD | EURUSD
    initialBalance: 400
    sizing: "Vol Target"            # "Vol Target" enables the vol* sweeps
    baseLot: "0.2,0.3,0.4,0.5,0.6"
    leverage: "1"
    volTarget: "0.2,0.25,0.3,0.35,0.4"
    volHalflife: "10,20,30,40,50"
    volMaxMult: "2,3,4"
    volMinDays: "20,40,60,80,100"
    spread: 0.2
    slippage: 0
    fromDate: "2018-01-01"
    toDate: "2026-01-01"
```

> Note: each list is capped at 16 values and the total grid at 10,000 combos by
> the backend. The runner reports a clear error if a grid is rejected and moves
> on to the next experiment.

## Execution

```bash
python runner.py
# or
python runner.py --config config.yaml --base-url http://localhost:8080
```

While a tune runs you get a live bar:

```
Experiment 2/5 BUY30  ███████████░░░░░  42%  780/1875  ETA 18m  24.6 bt/s  0:00:32
```

## Output

```
results/
  2026-06-30_14-10-20_RTH_VWAP/
      config.json     # the exact request body sent to /api/tune
      results.csv     # every combo, sorted by score desc
      results.json    # summary + every combo (?full=true)
      report.md       # Markdown report (best config + Top 20)
      heatmap.json    # volTarget × volHalflife average-score surface
  2026-06-30_16-40-55_BUY30/
      ...
  summary.csv         # one row per experiment (written at the end)
```

`summary.csv` columns:

| Experiment | Status | Best Score | Best Sharpe | Best PF | Best Drawdown | Best Return | Elapsed | Error |
|---|---|---|---|---|---|---|---|---|

## Behavior

- **Sequential.** One experiment at a time (the backend tuner is a single global
  job), which is exactly the model this runner targets.
- **Failure isolation.** A failed experiment is recorded (error saved, row marked
  `failed`) and the suite continues with the next one.
- **Retries.** Submit / status / download HTTP calls retry up to 3 times.
- **No poll timeout.** Polling continues with exponential backoff until the job
  reports `completed` or `failed`.

## Architecture (runner.py)

| Class | Responsibility |
|---|---|
| `RunnerConfig` / `Experiment` / `PollConfig` / `HttpConfig` | Typed config models loaded from YAML. |
| `TuneClient` | Retrying HTTP wrapper over the tune endpoints. |
| `ExperimentRunner` | Runs one experiment: submit → poll (live bar) → download → save. |
| `Suite` | Iterates all experiments and writes `summary.csv`. |
