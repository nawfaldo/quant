"""Tune automation runner.

An external, read-only client for the quant backend's existing tuning API. It
reads ``config.yaml``, runs each experiment sequentially via ``POST /api/tune``,
polls progress with a live Rich bar, downloads the result artifacts, and writes a
roll-up ``summary.csv``. The backend is never modified — this only speaks HTTP.

Workflow per experiment:
    POST /api/tune → poll /api/tune/status until completed
    → download results.csv / results.json / report.md / heatmap.json
    → save to results/<timestamp>_<name>/ → next experiment

Run:  python runner.py            (uses ./config.yaml)
      python runner.py --config other.yaml --base-url http://host:8080
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

console = Console()
log = logging.getLogger("runner")


# ── Configuration models ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PollConfig:
    """Exponential-backoff cadence for status polling (no overall timeout)."""

    initial_seconds: float = 1.0
    max_seconds: float = 10.0
    backoff: float = 1.5

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PollConfig":
        return cls(
            initial_seconds=float(d.get("initial_seconds", 1.0)),
            max_seconds=float(d.get("max_seconds", 10.0)),
            backoff=float(d.get("backoff", 1.5)),
        )


@dataclass(frozen=True)
class HttpConfig:
    """Retry policy for transient HTTP failures."""

    retries: int = 3
    retry_backoff_seconds: float = 2.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HttpConfig":
        return cls(
            retries=int(d.get("retries", 3)),
            retry_backoff_seconds=float(d.get("retry_backoff_seconds", 2.0)),
        )


# Keys forwarded to /api/tune. Everything is stringified (the backend tolerates
# numbers-as-strings, and list params MUST be strings), matching the frontend.
_TUNE_KEYS = (
    "strategy",
    "symbol",
    "initialBalance",
    "sizing",
    "baseLot",
    "leverage",
    "volTarget",
    "volHalflife",
    "volMaxMult",
    "volMinDays",
    "spread",
    "slippage",
    "fromDate",
    "toDate",
)


@dataclass(frozen=True)
class Experiment:
    """One named experiment and its raw tune parameters."""

    name: str
    params: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Experiment":
        name = str(d.get("name") or "experiment")
        params = {k: d[k] for k in _TUNE_KEYS if k in d}
        return cls(name=name, params=params)

    def request_body(self) -> dict[str, str]:
        """The JSON body for /api/tune — every value stringified."""
        return {k: str(v) for k, v in self.params.items()}

    def expected_combos(self) -> int:
        """Cartesian-product size of this experiment's grid — mirrors the
        backend's own total computation. Used to detect state contamination:
        if the running/downloaded job's size differs, a different job's results
        leaked in (the backend tuner is a single global job with no guard)."""
        def n(key: str, default: int = 1) -> int:
            raw = str(self.params.get(key, "")).strip()
            if not raw:
                return default
            return len([t for t in raw.replace(" ", ",").split(",") if t])

        base = n("baseLot")
        lev = n("leverage", default=1)
        if str(self.params.get("sizing", "")).strip() == "Vol Target":
            vol = n("volTarget") * n("volHalflife") * n("volMaxMult") * n("volMinDays")
        else:
            vol = 1
        return base * lev * vol


@dataclass(frozen=True)
class RunnerConfig:
    base_url: str
    output_dir: Path
    poll: PollConfig
    http: HttpConfig
    experiments: list[Experiment]

    @classmethod
    def load(cls, path: Path) -> "RunnerConfig":
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        experiments = [Experiment.from_dict(e) for e in raw.get("experiments", [])]
        if not experiments:
            raise ValueError(f"No experiments defined in {path}")
        return cls(
            base_url=str(raw.get("base_url", "http://localhost:8080")).rstrip("/"),
            output_dir=Path(raw.get("output_dir", "results")),
            poll=PollConfig.from_dict(raw.get("poll", {})),
            http=HttpConfig.from_dict(raw.get("http", {})),
            experiments=experiments,
        )


# ── HTTP client ───────────────────────────────────────────────────────────────


class TuneApiError(RuntimeError):
    """Raised when the backend rejects a request or returns an error payload."""


class TuneClient:
    """Thin, retrying HTTP wrapper over the backend's tune endpoints."""

    def __init__(self, base_url: str, http: HttpConfig) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Issue a request, retrying transient failures up to ``http.retries``."""
        url = f"{self._base}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._http.retries + 1):
            try:
                resp = self._session.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self._http.retries:
                    delay = self._http.retry_backoff_seconds * attempt
                    log.warning(
                        "%s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                        method, path, attempt, self._http.retries, exc, delay,
                    )
                    time.sleep(delay)
        raise TuneApiError(f"{method} {path} failed after {self._http.retries} attempts: {last_exc}")

    def submit(self, body: dict[str, str]) -> None:
        """POST /api/tune. Raises if the backend rejects the grid."""
        resp = self._request("POST", "/api/tune", json=body)
        data = resp.json()
        if data.get("error"):
            raise TuneApiError(f"backend rejected tune: {data['error']}")
        if not data.get("ok"):
            raise TuneApiError(f"unexpected submit response: {data}")

    def status(self) -> dict[str, Any]:
        """GET /api/tune/status — the live job state."""
        return self._request("GET", "/api/tune/status").json()

    def download(self, path: str) -> bytes:
        """GET a raw artifact (csv/json/md) as bytes."""
        return self._request("GET", path).content

    def save_run(self, body: dict[str, str]) -> int:
        """POST /api/run/save — run a single backtest and persist it to app.db
        (so it appears under /stats). Returns the new backtest id. This is the
        RUN path, not the tune path, so it never touches the tuner's global
        state."""
        resp = self._request("POST", "/api/run/save", json=body)
        data = resp.json()
        if data.get("error"):
            raise TuneApiError(f"save failed: {data['error']}")
        if "id" not in data:
            raise TuneApiError(f"unexpected save response: {data}")
        return int(data["id"])


# ── Result model ──────────────────────────────────────────────────────────────


@dataclass
class ExperimentResult:
    """Outcome of one experiment, used to build summary.csv."""

    name: str
    status: str  # "completed" | "failed"
    output_dir: Optional[Path] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    best_score: Optional[float] = None
    best_score_return: Optional[float] = None  # return % of the best-by-score config
    best_sharpe: Optional[float] = None
    best_profit_factor: Optional[float] = None
    best_drawdown: Optional[float] = None
    best_return: Optional[float] = None

    def summary_row(self) -> dict[str, Any]:
        return {
            "Experiment": self.name,
            "Status": self.status,
            "Best Score": self.best_score,
            # Profit % of the best-by-score config — the one --save-best persists.
            # (Distinct from "Best Return", which is the highest-return combo.)
            "Best Score Profit %": self.best_score_return,
            "Best Sharpe": self.best_sharpe,
            "Best PF": self.best_profit_factor,
            "Best Drawdown": self.best_drawdown,
            "Best Return": self.best_return,
            "Elapsed": _format_duration(self.elapsed_seconds),
            "Error": self.error or "",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


_ARTIFACTS: dict[str, str] = {
    # local filename → backend path. results.json uses ?full=true so the saved
    # archive contains every combo plus the summary (results.csv also has them).
    "results.csv": "/api/tune/results.csv",
    "results.json": "/api/tune/results.json?full=true",
    "report.md": "/api/tune/report.md",
    "heatmap.json": "/api/tune/heatmap.json",
}


# The six swept dimensions a tune best-combo carries; the rest of the run body
# (strategy, symbol, balance, costs, dates, sizing) comes from the experiment's
# base config unchanged.
_SAVE_SWEPT = ("baseLot", "leverage", "volTarget", "volHalflife", "volMaxMult", "volMinDays")


def _build_save_body(base: dict[str, Any], best: dict[str, Any]) -> dict[str, str]:
    """Build a /api/run/save body: the experiment's base params (from its saved
    config.json) with the swept fields pinned to the single best-combo values."""
    body = {k: str(v) for k, v in base.items() if k in _TUNE_KEYS}
    for key in _SAVE_SWEPT:
        if best.get(key) is not None:
            body[key] = str(best[key])
    return body


def _format_eta(ms: float) -> str:
    if not ms or ms <= 0:
        return "--"
    return _format_duration(ms / 1000.0)


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_summary(result: ExperimentResult, status: dict[str, Any]) -> None:
    """Pull the headline 'best of each metric' numbers from a completed status."""
    result.elapsed_seconds = float(status.get("elapsed", 0)) / 1000.0
    summary = status.get("summary") or {}
    if not isinstance(summary, dict):
        return

    def pick(block: str, metric: str) -> Optional[float]:
        node = summary.get(block)
        return _safe_float(node.get(metric)) if isinstance(node, dict) else None

    result.best_score = pick("bestByScore", "score")
    result.best_score_return = pick("bestByScore", "returnPct")
    result.best_sharpe = pick("bestSharpe", "sharpe")
    result.best_profit_factor = pick("bestProfitFactor", "profitFactor")
    result.best_drawdown = pick("lowestDrawdown", "maxDrawdown")
    result.best_return = pick("bestGrowth", "returnPct")


# ── Experiment runner ─────────────────────────────────────────────────────────


class ExperimentRunner:
    """Runs a single experiment: submit → poll (with live bar) → download → save."""

    def __init__(self, client: TuneClient, cfg: RunnerConfig) -> None:
        self._client = client
        self._cfg = cfg

    def run(self, experiment: Experiment, index: int, total: int) -> ExperimentResult:
        label = f"Experiment {index}/{total}: {experiment.name}"
        log.info(label)
        result = ExperimentResult(name=experiment.name, status="failed")
        expected = experiment.expected_combos()

        out_dir = self._make_output_dir(experiment)
        result.output_dir = out_dir
        self._save_json(out_dir / "config.json", experiment.request_body())

        # GUARD 1 — never submit while the backend is busy. The tuner is a single
        # global job with no concurrency lock; submitting over a running job (or a
        # stale detached one from an interrupted run) cross-contaminates results.
        try:
            self._wait_until_idle()
            self._client.submit(experiment.request_body())
        except TuneApiError as exc:
            result.error = str(exc)
            log.error("Submit failed for %s: %s", experiment.name, exc)
            return result

        try:
            final = self._poll_until_done(experiment, index, total, expected)
        except TuneApiError as exc:
            result.error = str(exc)
            log.error("Polling failed for %s: %s", experiment.name, exc)
            return result

        if final.get("status") != "completed":
            result.error = str(final.get("error") or "tune failed")
            log.error("Experiment %s failed: %s", experiment.name, result.error)
            return result

        self._download_artifacts(out_dir)

        # GUARD 3 — the saved CSV must have exactly the grid we asked for. If the
        # row count differs, the artifacts belong to a different job (the BUY30
        # contamination signature) — fail loudly instead of saving wrong data.
        rows = self._csv_row_count(out_dir / "results.csv")
        if rows is not None and rows != expected:
            result.error = (
                f"result mismatch: expected {expected} combos, downloaded {rows} "
                f"(state contamination — re-run this experiment in isolation)"
            )
            log.error("%s: %s", experiment.name, result.error)
            return result

        _extract_summary(result, final)
        result.status = "completed"
        log.info(
            "%s completed in %s — saved to %s",
            experiment.name, _format_duration(result.elapsed_seconds), out_dir,
        )
        return result

    def _wait_until_idle(self) -> None:
        """Block until the backend reports no running tune (no overall timeout,
        exponential backoff). Guarantees a clean single-job submit."""
        interval = self._cfg.poll.initial_seconds
        first = True
        while self._client.status().get("status") == "running":
            if first:
                log.info("Backend busy with another tune — waiting for it to finish…")
                first = False
            time.sleep(interval)
            interval = min(interval * self._cfg.poll.backoff, self._cfg.poll.max_seconds)

    @staticmethod
    def _csv_row_count(path: Path) -> Optional[int]:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        return max(0, len(lines) - 1)  # minus header

    def _make_output_dir(self, experiment: Experiment) -> Path:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_dir = self._cfg.output_dir / f"{stamp}_{experiment.name}"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir

    def _poll_until_done(
        self, experiment: Experiment, index: int, total: int, expected: int
    ) -> dict[str, Any]:
        """Poll status with exponential backoff, rendering a live progress bar.

        Guard 2: the first time the backend reports a grid total, verify it equals
        the grid we submitted. A mismatch means our POST didn't take effect / a
        different job's state is being reported — abort rather than track it."""
        interval = self._cfg.poll.initial_seconds
        verified = False
        columns = (
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("ETA [cyan]{task.fields[eta]}"),
            TextColumn("[green]{task.fields[throughput]:.1f} bt/s"),
            TimeElapsedColumn(),
        )
        with Progress(*columns, console=console, transient=False) as progress:
            task = progress.add_task(
                f"Exp {index}/{total} {experiment.name}",
                total=None, eta="--", throughput=0.0,
            )
            while True:
                status = self._client.status()
                state = status.get("status")

                if state == "running":
                    grid_total = int(status.get("total") or 0) or None
                    if not verified and grid_total:
                        if grid_total != expected:
                            raise TuneApiError(
                                f"grid mismatch: submitted {expected} combos but backend "
                                f"is running {grid_total} (state contamination / overlap)"
                            )
                        verified = True
                    done = int(status.get("completed", status.get("progress", 0)))
                    progress.update(
                        task,
                        total=grid_total,
                        completed=done,
                        eta=_format_eta(_safe_float(status.get("estimatedRemaining")) or 0),
                        throughput=_safe_float(status.get("throughput")) or 0.0,
                    )
                    time.sleep(interval)
                    interval = min(interval * self._cfg.poll.backoff, self._cfg.poll.max_seconds)
                    continue

                if state == "completed":
                    grid_total = int(status.get("total") or 0) or 1
                    progress.update(task, total=grid_total, completed=grid_total, eta="0s")
                    return status

                # failed / unknown
                return status

    def _download_artifacts(self, out_dir: Path) -> None:
        for filename, path in _ARTIFACTS.items():
            try:
                content = self._client.download(path)
                (out_dir / filename).write_bytes(content)
            except TuneApiError as exc:
                log.warning("Could not download %s: %s", filename, exc)

    @staticmethod
    def _save_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ── Suite orchestration ───────────────────────────────────────────────────────


class Suite:
    """Runs every configured experiment in order and writes summary.csv."""

    def __init__(self, cfg: RunnerConfig) -> None:
        self._cfg = cfg
        self._client = TuneClient(cfg.base_url, cfg.http)
        self._runner = ExperimentRunner(self._client, cfg)

    def run(self, ask_save_best: bool = True) -> list[ExperimentResult]:
        total = len(self._cfg.experiments)
        log.info("Running %d experiment(s) against %s", total, self._cfg.base_url)
        results: list[ExperimentResult] = []
        for i, experiment in enumerate(self._cfg.experiments, start=1):
            console.rule(f"[bold]{i}/{total}  {experiment.name}")
            # One failure never stops the suite.
            result = self._runner.run(experiment, i, total)
            results.append(result)
        self._write_summary(results)
        if ask_save_best:
            self._prompt_and_save(results)
        return results

    # ── Saving the best config of each experiment to /stats ───────────────────

    def _prompt_and_save(self, results: list[ExperimentResult]) -> None:
        """After a run, offer to persist each experiment's best (by score) config
        as a saved backtest. One yes/no for the whole set."""
        savable = [r for r in results if r.status == "completed" and r.output_dir]
        if not savable:
            return
        try:
            answer = input(
                f"\nSave the best (by score) config of {len(savable)} experiment(s) "
                f"to saved backtests (view at /stats)? [y/N]: "
            ).strip().lower()
        except EOFError:
            answer = "n"
        if answer not in ("y", "yes"):
            log.info("Not saving best configs.")
            return
        self._save_best([r.output_dir for r in savable if r.output_dir])

    def save_best_from_disk(self) -> None:
        """Standalone: for every experiment in the config, find its most recent
        results folder on disk, save that run's best combo, and rebuild
        summary.csv from those folders. Lets you persist an already-finished
        suite (and refresh the summary, e.g. for new columns) without re-running."""
        out = self._cfg.output_dir
        if not out.exists():
            log.error("No results directory: %s", out)
            return
        pairs = [
            (e, f) for e in self._cfg.experiments
            if (f := self._latest_folder(out, e.name)) is not None
        ]
        if not pairs:
            log.error("No result folders found under %s", out)
            return

        # Refresh summary.csv from disk first (fast), then save (network-bound).
        results = [
            r for exp, folder in pairs
            if (r := self._result_from_folder(exp.name, folder)) is not None
        ]
        if results:
            self._write_summary(results)
        self._save_best([f for _, f in pairs])

    @staticmethod
    def _result_from_folder(name: str, folder: Path) -> Optional[ExperimentResult]:
        """Reconstruct an ExperimentResult (for summary.csv) from a folder's saved
        results.json, reusing the same extraction as a live run."""
        res_path = folder / "results.json"
        if not res_path.exists():
            return None
        try:
            data = json.loads(res_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        result = ExperimentResult(name=name, status="completed", output_dir=folder)
        # results.json carries {elapsedMs, summary{...}}; map it to the shape
        # _extract_summary expects from a /status payload.
        _extract_summary(result, {"elapsed": data.get("elapsedMs", 0), "summary": data.get("summary")})
        return result

    def _save_best(self, folders: list[Path]) -> None:
        saved = 0
        for folder in folders:
            cfg_path, res_path = folder / "config.json", folder / "results.json"
            if not (cfg_path.exists() and res_path.exists()):
                log.warning("Incomplete results in %s — skipping", folder.name)
                continue
            try:
                base = json.loads(cfg_path.read_text(encoding="utf-8"))
                results = json.loads(res_path.read_text(encoding="utf-8"))
                best = (results.get("summary") or {}).get("bestByScore")
                if not isinstance(best, dict):
                    log.warning("No bestByScore in %s — skipping", folder.name)
                    continue
                new_id = self._client.save_run(_build_save_body(base, best))
                log.info("Saved best of %s → backtest #%d", folder.name, new_id)
                saved += 1
            except (TuneApiError, ValueError, KeyError, OSError) as exc:
                log.error("Could not save %s: %s", folder.name, exc)
        if saved:
            console.print(
                f"\n[bold green]Saved {saved} backtest(s).[/] "
                f"View them at [cyan]http://localhost:5173/stats[/]"
            )

    @staticmethod
    def _latest_folder(out: Path, name: str) -> Optional[Path]:
        """Most recent `<timestamp>_<name>` folder (timestamps sort lexically)."""
        matches = sorted(
            p for p in out.iterdir() if p.is_dir() and p.name.endswith(f"_{name}")
        )
        return matches[-1] if matches else None

    def _write_summary(self, results: list[ExperimentResult]) -> None:
        self._cfg.output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = self._cfg.output_dir / "summary.csv"
        df = pd.DataFrame([r.summary_row() for r in results])
        df.to_csv(summary_path, index=False)
        log.info("Wrote suite summary → %s", summary_path)

        completed = sum(1 for r in results if r.status == "completed")
        console.rule("[bold]Suite complete")
        console.print(df.to_string(index=False))
        console.print(
            f"\n[bold green]{completed}[/] completed, "
            f"[bold red]{len(results) - completed}[/] failed. "
            f"Summary: {summary_path}"
        )


# ── Entry point ───────────────────────────────────────────────────────────────


def _force_utf8_output() -> None:
    """Windows consoles default to cp1252 and crash on the box/bar/dash glyphs
    Rich emits (and on redirected/piped output). Force UTF-8 so it works
    everywhere — a real terminal, a pipe, or a file redirect."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _configure_logging() -> None:
    _force_utf8_output()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run quant tuning experiments sequentially.")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--base-url", type=str, default=None, help="Override base_url from the config.")
    parser.add_argument(
        "--save-best",
        action="store_true",
        help="Don't run anything; save the best (by score) config of each experiment's "
             "most recent results folder to /stats. Use this to persist an already-finished run.",
    )
    return parser.parse_args()


def main() -> int:
    _configure_logging()
    args = _parse_args()
    try:
        cfg = RunnerConfig.load(args.config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.error("Failed to load config %s: %s", args.config, exc)
        return 2

    if args.base_url:
        cfg = RunnerConfig(
            base_url=args.base_url.rstrip("/"),
            output_dir=cfg.output_dir,
            poll=cfg.poll,
            http=cfg.http,
            experiments=cfg.experiments,
        )

    try:
        suite = Suite(cfg)
        if args.save_best:
            suite.save_best_from_disk()
        else:
            suite.run()
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
